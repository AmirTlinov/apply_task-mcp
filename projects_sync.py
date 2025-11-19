import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import yaml

GRAPHQL_URL = "https://api.github.com/graphql"
CONFIG_PATH = Path(".apply_task_projects.yaml")
logger = logging.getLogger("apply_task.projects")


@dataclass
class FieldConfig:
    name: str
    options: Dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    project_type: str
    owner: str
    number: int
    repo: Optional[str] = None
    fields: Dict[str, FieldConfig] = field(default_factory=dict)


class ProjectsSync:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = config_path or CONFIG_PATH
        self.config: Optional[ProjectConfig] = self._load_config()
        self.token = os.getenv("APPLY_TASK_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
        self.session = requests.Session()
        self.project_id: Optional[str] = None
        self.project_fields: Dict[str, Dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.token)

    def sync_task(self, task) -> bool:
        if not self.enabled:
            return False
        try:
            self._ensure_project_metadata()
        except Exception as exc:  # pragma: no cover - defensive log
            logger.warning("projects sync disabled: %s", exc)
            return False

        changed = False
        body = self._build_body(task)
        if not getattr(task, "project_item_id", None):
            item_id, draft_id = self._create_draft_issue(task, body)
            if item_id:
                task.project_item_id = item_id
                changed = True
            if draft_id:
                task.project_draft_id = draft_id
                changed = True
        else:
            self._update_draft_issue(task, body)

        if getattr(task, "project_item_id", None):
            self._update_fields(task)

        if changed:
            self._persist_metadata(task)
        return changed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_config(self) -> Optional[ProjectConfig]:
        if not self.config_path.exists():
            return None
        try:
            data = yaml.safe_load(self.config_path.read_text()) or {}
        except Exception as exc:  # pragma: no cover - config errors
            logger.warning("invalid projects config: %s", exc)
            return None
        project = data.get("project") or {}
        fields_cfg = {}
        for alias, cfg in (data.get("fields") or {}).items():
            fields_cfg[alias] = FieldConfig(name=cfg.get("name", alias), options=cfg.get("options", {}))
        try:
            project_type = (project.get("type") or "repository").lower()
            owner = project["owner"]
            number = int(project.get("number", 1))
            repo = project.get("repo")
        except KeyError:
            return None
        return ProjectConfig(project_type=project_type, owner=owner, number=number, repo=repo, fields=fields_cfg)

    def _graphql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"bearer {self.token}", "Accept": "application/vnd.github+json"}
        response = self.session.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub API error: {response.status_code} {response.text}")
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        return payload["data"]

    def _ensure_project_metadata(self) -> None:
        if self.project_id:
            return
        cfg = self.config
        if not cfg:
            raise RuntimeError("projects config missing")
        if cfg.project_type == "repository":
            if not cfg.repo:
                raise RuntimeError("repo is required for repository projects")
            query = self._repo_project_query()
            variables = {"owner": cfg.owner, "name": cfg.repo, "number": cfg.number}
            data = self._graphql(query, variables)
            node = (data.get("repository") or {}).get("projectV2")
        elif cfg.project_type == "organization":
            query = self._org_project_query()
            variables = {"login": cfg.owner, "number": cfg.number}
            data = self._graphql(query, variables)
            node = (data.get("organization") or {}).get("projectV2")
        else:  # user
            query = self._user_project_query()
            variables = {"login": cfg.owner, "number": cfg.number}
            data = self._graphql(query, variables)
            node = (data.get("user") or {}).get("projectV2")
        if not node:
            raise RuntimeError("project not found")
        self.project_id = node.get("id")
        field_nodes = ((node.get("fields") or {}).get("nodes") or [])
        self.project_fields = self._map_fields(field_nodes)

    def _repo_project_query(self) -> str:
        return (
            "query($owner:String!,$name:String!,$number:Int!){\n"
            "  repository(owner:$owner,name:$name){\n"
            "    projectV2(number:$number){\n"
            "      id title\n"
            "      fields(first:50){nodes{__typename id name dataType ... on ProjectV2SingleSelectField { options(first:50){ id name } }}}\n"
            "    }\n"
            "  }\n"
            "}"
        )

    def _org_project_query(self) -> str:
        return (
            "query($login:String!,$number:Int!){ organization(login:$login){ projectV2(number:$number){ id title fields(first:50){nodes{__typename id name dataType ... on ProjectV2SingleSelectField { options(first:50){ id name } }}}}} }"
        )

    def _user_project_query(self) -> str:
        return (
            "query($login:String!,$number:Int!){ user(login:$login){ projectV2(number:$number){ id title fields(first:50){nodes{__typename id name dataType ... on ProjectV2SingleSelectField { options(first:50){ id name } }}}}} }"
        )

    def _map_fields(self, nodes: Any) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        cfg_fields = self.config.fields if self.config else {}
        for alias, field_cfg in cfg_fields.items():
            match = next((n for n in nodes if n.get("name") == field_cfg.name), None)
            if not match:
                continue
            entry = {
                "id": match.get("id"),
                "typename": match.get("__typename"),
                "options": {},
            }
            if match.get("__typename") == "ProjectV2SingleSelectField":
                entry["options" ] = {opt.get("name"): opt.get("id") for opt in (match.get("options") or [])}
            result[alias] = entry
        return result

    def _create_draft_issue(self, task, body: str) -> (Optional[str], Optional[str]):
        mutation = (
            "mutation($projectId:ID!,$title:String!,$body:String!){"
            "  createProjectV2DraftIssue(input:{projectId:$projectId,title:$title,body:$body}){"
            "    projectItem{ id } draftIssue{ id }"
            "  }"
            "}"
        )
        variables = {"projectId": self.project_id, "title": f"{task.id}: {task.title}", "body": body}
        try:
            data = self._graphql(mutation, variables)["createProjectV2DraftIssue"]
            item = (data or {}).get("projectItem") or {}
            draft = (data or {}).get("draftIssue") or {}
            return item.get("id"), draft.get("id")
        except Exception as exc:  # pragma: no cover - network failure
            logger.warning("GitHub draft creation failed: %s", exc)
            return None, None

    def _ensure_draft_id(self, task) -> Optional[str]:
        if getattr(task, "project_draft_id", None):
            return task.project_draft_id
        query = (
            "query($item:ID!){ node(id:$item){ ... on ProjectV2Item { content { __typename ... on DraftIssue { id } } } } }"
        )
        try:
            data = self._graphql(query, {"item": task.project_item_id})
            node = (data.get("node") or {}).get("content") or {}
            if node.get("__typename") == "DraftIssue":
                task.project_draft_id = node.get("id")
        except Exception as exc:  # pragma: no cover - network failure
            logger.warning("Unable to fetch draft issue id: %s", exc)
        return task.project_draft_id

    def _update_draft_issue(self, task, body: str) -> None:
        draft_id = self._ensure_draft_id(task)
        if not draft_id:
            return
        mutation = (
            "mutation($projectId:ID!,$draftId:ID!,$title:String!,$body:String!){"
            "  updateProjectV2DraftIssue(input:{projectId:$projectId,draftIssueId:$draftId,title:$title,body:$body}){ draftIssue{ id } }"
            "}"
        )
        variables = {
            "projectId": self.project_id,
            "draftId": draft_id,
            "title": f"{task.id}: {task.title}",
            "body": body,
        }
        try:
            self._graphql(mutation, variables)
        except Exception as exc:  # pragma: no cover
            logger.warning("GitHub draft update failed: %s", exc)

    def _update_fields(self, task) -> None:
        for alias, field in self.project_fields.items():
            field_cfg = self.config.fields.get(alias) if self.config else None
            if not field_cfg:
                continue
            value = None
            if field["typename"] == "ProjectV2SingleSelectField":
                desired = field_cfg.options.get(task.status)
                option_id = field["options"].get(desired)
                if option_id:
                    value = {"singleSelectOptionId": option_id}
            elif field["typename"] == "ProjectV2NumberField":
                if alias == "progress":
                    value = {"number": task.calculate_progress()}
            else:  # text/datetime
                if alias == "domain":
                    value = {"text": task.domain or "-"}
                elif alias == "subtasks":
                    total = len(task.subtasks)
                    completed = sum(1 for st in task.subtasks if st.completed)
                    value = {"text": f"{completed}/{total}" if total else "-"}
            if value is None:
                continue
            mutation = (
                "mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$value:ProjectV2FieldValue!){"
                "  updateProjectV2ItemFieldValue(input:{projectId:$projectId,itemId:$itemId,fieldId:$fieldId,value:$value}){ projectV2Item{ id } }"
                "}"
            )
            variables = {
                "projectId": self.project_id,
                "itemId": task.project_item_id,
                "fieldId": field["id"],
                "value": value,
            }
            try:
                self._graphql(mutation, variables)
            except Exception as exc:  # pragma: no cover
                logger.warning("Field update failed (%s): %s", alias, exc)

    def _persist_metadata(self, task) -> None:
        try:
            task.filepath.write_text(task.to_file_content(), encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            logger.warning("Unable to persist project metadata: %s", exc)

    def _build_body(self, task) -> str:
        lines = [
            f"# {task.id}: {task.title}",
            "",
            f"- Status: {task.status}",
            f"- Domain: {task.domain or '—'}",
            f"- Progress: {task.calculate_progress()}%",
        ]
        if getattr(task, "description", ""):
            lines += ["", "## Description", task.description]
        if task.subtasks:
            lines += ["", "## Subtasks"]
            for sub in task.subtasks:
                mark = "x" if sub.completed else " "
                crit = "✓" if sub.criteria_confirmed else "·"
                tests = "✓" if sub.tests_confirmed else "·"
                blockers = "✓" if sub.blockers_resolved else "·"
                lines.append(f"- [{mark}] {sub.title} [criteria {crit} | tests {tests} | blockers {blockers}]")
        if getattr(task, "success_criteria", None):
            lines += ["", "## Success criteria", *[f"- {item}" for item in task.success_criteria]]
        if getattr(task, "risks", None):
            lines += ["", "## Risks", *[f"- {r}" for r in task.risks]]
        return "\n".join(lines).strip()


def get_projects_sync() -> ProjectsSync:
    global _PROJECTS_SYNC
    try:
        return _PROJECTS_SYNC
    except NameError:
        _PROJECTS_SYNC = ProjectsSync()
        return _PROJECTS_SYNC
