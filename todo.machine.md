# Project Status Tracker

## Core Systems

- [x] 1. Scrollback & search | OK | Scrollback + search >> .tasks/TASK-001.task
- [x] 2. Cursor tracker (VTE) | OK | Cursor + ANSI snapshot
- [x] 3. ANSI grid parser | OK | 8/16/256/RGB + CSI
- [x] 4. ANSI SGR attrs | OK | Bold/Faint/Inverse/Underline
- [x] 5. Text renderer (atlas/SDF/MSDF) | OK | Atlas 2048x2048 + IPU 40%
- [!] 6. Text shaping… | WARN | Monospace + rustybuzz >> .tasks/TASK-006.task
- [x] 7. Render batching… | OK | ColorGrid + glyph batches
- [x] 8. Renderer: WGPU pipelines | OK | rect/underline/text pipelines
- [x] 9. Widget integration… | OK | Adapter/SessionManager flow

## Integration & Quality

- [!] 10. IME cursor mapping | WARN | IME rectangle derived from props
- [!] 11. Input: selection | WARN | Basic mouse selection in progress
- [!] 12. Input: clipboard | WARN | Copy/paste wired (Ctrl+Shift+C/V)
- [!] 13. Input: mouse reporting (SGR) | WARN | SGR events delivered (buttons/modifiers)
- [!] 14. Alternate screen buffer | WARN | Alt buffer skeleton implemented
- [!] 15. Perf: dirty regions | WARN | Dirty bounds tracking + hashes
- [x] 16. Perf: frame pacing/telemetry | OK | Adaptive pacer present
- [x] 17. Assets & hashing | OK | apex_assets + BLAKE3

## Advanced Features

- [!] 18. Accessibility (roles/hit) | WARN | a11y crate not integrated
- [!] 19. Headless parity & CLI fixtures | WARN | Partial parity only
- [!] 20. Platform I/O & DPI | WARN | DPI/input gaps remain
- [!] 21. Keyboard/Mouse completeness | WARN | Some shortcuts missing
- [!] 22. Layout/Scene invariants | WARN | Scene diff/validation WIP
- [!] 23. CPU fallback/offscreen | WARN | Fallback not automated
- [!] 24. Complex text (Ligatures/bidi/CJK) | WARN | Limited coverage
- [x] 25. DEC private modes | FAIL | Missing DECRST/DECSET >> .tasks/TASK-015.task
- [!] 26. Telemetry & regression hashes | WARN | Partial metrics
- [x] 27. Safety & log sanitization | FAIL | Control chars not filtered
- [x] 28. Config/themes/profiles | WARN | Config surface too small
- [x] 29. Persistence & recovery | FAIL | No state save/restore
- [!] 30. Configurable hotkeys & UX | WARN | Shortcut catalog incomplete
