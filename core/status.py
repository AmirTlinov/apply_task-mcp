from enum import Enum


class Status(Enum):
    OK = ("OK", "green", "+")
    WARN = ("WARN", "yellow", "~")
    FAIL = ("FAIL", "red", "x")
    UNKNOWN = ("?", "blue", "?")

    @classmethod
    def from_string(cls, value: str) -> "Status":
        val = (value or "").strip().upper()
        for status in cls:
            if status.value[0] == val:
                return status
        return cls.UNKNOWN
