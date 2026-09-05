from __future__ import annotations
import re
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [(re.compile('rm\\s+-[a-z]*r[a-z]*f[a-z]*\\s+/\\s*$'), '递归强制删除根目录'), (re.compile('mkfs\\.'), '格式化磁盘'), (re.compile('dd\\s+if=.*of=/dev/'), '直接写磁盘设备'), (re.compile('chmod\\s+-R\\s+777\\s+/'), '递归修改根目录权限'), (re.compile(':\\(\\)\\{\\s*:\\|:&\\s*\\};:'), 'fork bomb'), (re.compile('curl\\s+.*\\|\\s*(ba)?sh'), '管道执行远程脚本'), (re.compile('wget\\s+.*\\|\\s*(ba)?sh'), '管道执行远程脚本'), (re.compile('>\\s*/dev/sd'), '覆盖磁盘设备')]
_SAFE_COMMANDS = frozenset({'ls', 'dir', 'pwd', 'echo', 'cat', 'head', 'tail', 'wc', 'find', 'which', 'whereis', 'whoami', 'hostname', 'uname', 'date', 'cal', 'uptime', 'df', 'du', 'free', 'env', 'printenv', 'file', 'stat', 'readlink', 'realpath', 'basename', 'dirname', 'sort', 'uniq', 'tr', 'cut', 'awk', 'sed', 'grep', 'egrep', 'fgrep', 'diff', 'comm', 'tee', 'xargs', 'true', 'false', 'test', 'git status', 'git log', 'git diff', 'git show', 'git branch', 'git tag', 'git remote', 'git rev-parse', 'git ls-files', 'git blame', 'git stash list', 'go version', 'go env', 'node -v', 'npm -v', 'npx', 'python --version', 'pip list', 'cargo --version', 'rustc --version', 'java -version', 'java --version'})

def is_safe_command(command: str) -> bool:
    return command.strip() in {'pwd', 'whoami', 'uname', 'git status', 'git status --short', 'git diff', 'git diff --stat', 'git diff --cached', 'git ls-files', 'python --version', 'python3 --version', 'node --version'}

class DangerousCommandDetector:

    def __init__(self, extra_patterns: list[tuple[str, str]] | None=None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))

    def detect(self, command: str) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return (True, reason)
        return (False, '')
