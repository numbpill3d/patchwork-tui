#!/usr/bin/env python3
"""
PATCHWORK — repo-wide refactor queue TUI.

Queue up to 15 related file edits, inspect every hunk, reject what
should not ship, then commit the survivors as one operation.

  j/k or Up/Down .. navigate      TAB .......... cycle pane
  SPACE ........... stage/unstage hunk    a .... approve file
  x ............... reject hunk          A .... approve all
  C ............... commit (one op)      ? .... help
  q ............... quit                 1/2/3  jump to pane

Run:  python3 patchwork.py          (live git in a repo, else demo queue)
      python3 patchwork.py --demo  (force demo queue anywhere)
      python3 patchwork.py --splash (startup banner only, no TTY needed)
"""
import curses, os, subprocess, sys, textwrap, time

# ── live git layer (falls back to demo queue outside a repo) ─────────
def _git(*args, cwd=None):
    try:
        r = subprocess.run(["git", *args], cwd=cwd or os.getcwd(),
                           capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)

def git_root():
    rc, out, _ = _git("rev-parse", "--show-toplevel")
    return out.strip() if rc == 0 and out.strip() else None

def git_branch(root):
    rc, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    return out.strip() if rc == 0 else "?"

def load_live_queue(root, cap=15):
    """Real changed files -> (files, diffs, reasons). None if clean/not repo."""
    rc, out, _ = _git("status", "--porcelain", cwd=root)
    if rc != 0 or not out.strip():
        return None
    entries = []
    for ln in out.splitlines():
        if len(ln) < 4:  # NOTE: no .strip() — cols 0/1 are status flags
            continue
        flag = (ln[0] + ln[1]).strip() or "M"
        fn = ln[3:].strip().strip('"')
        if " -> " in fn:
            fn = fn.split(" -> ")[-1]
        entries.append((fn, flag if len(flag) == 1 else flag[0]))
        if len(entries) >= cap:
            break
    files, diffs, reasons = [], {}, []
    for i, (fn, flag) in enumerate(entries):
        _, d, _ = _git("diff", "-U3", "--", fn, cwd=root)
        if not d.strip():
            _, d, _ = _git("diff", "--cached", "-U3", "--", fn, cwd=root)
        hunks = parse_unified(d, fn) or [{
            "head": "@@ binary / empty diff — stage whole file", "state": True,
            "lines": [(" ", "  (no text hunks; SPACE stages the file)")],
            "patch": None}]
        diffs[i] = hunks
        files.append((fn, flag, "%d hunk(s) from git diff" % len(hunks)))
        reasons.append("Change in %s: inspect each hunk below; "
                       "rejected hunks are excluded from the single commit." % fn)
    return files, diffs, reasons

def parse_unified(diff_text, fn):
    hunks, cur = [], None
    header = []
    for ln in diff_text.split("\n"):
        if ln.startswith("diff --git") or ln.startswith("index ") \
                or ln.startswith("--- ") or ln.startswith("+++ "):
            header.append(ln)
            continue
        if ln.startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = {"head": ln[:80], "state": True, "lines": [], "_body": [ln]}
        elif cur is not None:
            cur["_body"].append(ln)
            kind = ln[0] if ln and ln[0] in ("+", "-") else " "
            cur["lines"].append((kind, (ln[:100] if ln else "")))
    if cur:
        hunks.append(cur)
    for h in hunks:
        h["patch"] = "\n".join(header + h["_body"]) + "\n"
    return hunks

def stage_hunk(root, fn, patch):
    if not patch:
        rc, _, err = _git("add", "--", fn, cwd=root)
        return rc == 0, err.strip()
    p = subprocess.run(["git", "apply", "--cached", "--unidiff-zero", "-"],
                       input=patch, text=True, cwd=root,
                       capture_output=True, timeout=15)
    return p.returncode == 0, p.stderr.strip()

def git_commit(root, message):
    return _git("commit", "-m", message, cwd=root)

# ── repo picker (scan for git repos, choose one) ──────────────────────
SCAN_PRUNE_NAMES = ("node_modules", ".cache", ".mozilla", ".npm", ".cargo",
                    ".local", ".var", "venv", ".venv", "__pycache__",
                    ".steam", ".electron-gyp")

def scan_roots():
    extra = os.environ.get("PATCHWORK_SCAN_ROOTS", "")
    roots = [os.path.expanduser("~")]
    roots += [p for p in extra.split(":") if p.strip()]
    return [r for r in roots if os.path.isdir(r)]

def find_repos(limit=40):
    """All dirs containing a `.git` subdir under the scan roots."""
    depth = int(os.environ.get("PATCHWORK_SCAN_DEPTH", "4"))
    prune = []
    for i, name in enumerate(SCAN_PRUNE_NAMES):
        prune += (["-o"] if i else []) + ["-name", name]
    found = []
    skip_hidden = os.environ.get("PATCHWORK_SCAN_HIDDEN", "") != "1"
    for root in scan_roots():
        tests = list(prune)
        if skip_hidden:
            tests += ["-o", "(", "-name", ".*", "!", "-name", ".git", ")"]
        cmd = (["find", root, "-maxdepth", str(depth), "("] + tests +
               [")", "-prune", "-o", "-type", "d", "-name", ".git", "-print"])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for ln in r.stdout.splitlines():
            repo = os.path.dirname(ln.strip())
            if repo and repo not in found:
                found.append(repo)
            if len(found) >= limit:
                break
    return sorted(found)

def repo_info(repo):
    rc, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    branch = out.strip() if rc == 0 else "?"
    rc, out, _ = _git("status", "--porcelain", cwd=repo)
    dirty = len([l for l in out.splitlines() if l.strip()]) if rc == 0 else 0
    return branch, dirty

def pick_repo():
    """Interactive numbered list. Returns chosen path or None."""
    repos = find_repos()
    if not repos:
        print("No git repos found under: %s" % ", ".join(scan_roots()))
        return None
    infos = [(r,) + repo_info(r) for r in repos]
    for n, (r, b, d) in enumerate(infos, 1):
        state = "*%d dirty" % d if d else "clean"
        print("%2d. %-52s [%s] %s" % (n, r, b, state))
    try:
        sel = input("Select repo [1-%d] (ENTER cancels): " % len(infos)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not sel:
        return None
    try:
        idx = int(sel) - 1
    except ValueError:
        print("Not a number — cancelled.")
        return None
    if not 0 <= idx < len(infos):
        print("Out of range — cancelled.")
        return None
    return infos[idx][0]

# ── branding (one small mark; mascot lives in help only) ─────────────
LOGO = [
 "██████╗  █████╗ ████████╗ ██████╗██╗  ██╗██╗    ██╗ ██████╗ ██████╗ ██╗  ██╗",
 "██╔══██╗██╔══██╗╚══██╔══╝██╔════╝██║  ██║██║    ██║██╔═══██╗██╔══██╗██║ ██╔╝",
 "██████╔╝███████║   ██║   ██║     ███████║██║ █╗ ██║██║   ██║██████╔╝█████╔╝ ",
 "██╔═══╝ ██╔══██║   ██║   ██║     ██╔══██║██║███╗██║██║   ██║██╔══██╗██╔═██╗ ",
 "██║     ██║  ██║   ██║   ╚██████╗██║  ██║╚███╔███╔╝╚██████╔╝██║  ██║██║  ██╗",
 "╚═╝     ╚═╝  ╚═╝   ╚═╝    ╚═════╝╚═╝  ╚═╝ ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
]

MASCOT = r"""
  (\_/)
  ( ._.)  patchwork — queue many, inspect all, ship once.
  /> |>
"""

# ── demo queue (used with --demo or outside a git repo) ───────────────
FILES = [
 ("src/auth/login.ts",      "M", "migrate bcrypt -> argon2id, add passkey hook"),
 ("src/auth/session.ts",    "M", "sliding refresh, rotate on privilege bump"),
 ("src/auth/oauth.ts",      "M", "pkce strict, state nonce 32B"),
 ("src/api/middleware.ts",  "M", "attach principal, request-id tracing"),
 ("src/api/users.ts",       "M", "cursor pagination, zod strict"),
 ("src/api/billing.ts",     "M", "+ idempotency-key guard"),
 ("src/db/schema.prisma",   "M", "+ User.passkey, Session.rotatedAt"),
 ("src/db/migrate_014.sql", "A", "new migration for auth columns"),
 ("src/cache/redis.ts",     "M", "session ns v2, jittered TTL"),
 ("web/login.tsx",          "M", "passkey button + error states"),
 ("web/account.tsx",        "M", "sessions list + revoke-all"),
 ("tests/auth.test.ts",     "M", "+ 24 cases (replay, rotation)"),
 ("tests/api.test.ts",      "M", "+ idempotency + pagination cases"),
 ("docs/AUTH.md",           "M", "rewrite auth flow docs"),
 ("ops/flags.yaml",         "M", "auth_v2 rollout 10% -> 100%"),
]

DIFFS = {
 i: [
   {"head": "hunk 1: %s — %s" % (fn, desc[:38]), "state": True,
    "lines": [(" ", "  // patchwork: ai-refactor auth_v2"),
              ("-", "-  const hash = await bcrypt.hash(pw, 10);"),
              ("+", "+  const hash = await argon2.hash(pw, { type: 'id' });"),
              (" ", "   const u = await db.user.create({ data: { hash } });")]},
   {"head": "hunk 2: guard + test", "state": True,
    "lines": [(" ", "   if (!ctx.principal) throw new AuthError(401);"),
              ("-", "-  return handler(ctx);"),
              ("+", "+  return withIdempotency(ctx, () => handler(ctx));"),
              (" ", "  // + test: replay returns same key, no double-charge")]},
   {"head": "hunk 3: types/docs touch", "state": True,
    "lines": [(" ", "  export type Principal = { sub: string; scope: string[] };"),
              ("-", "-  // TODO: document rotation"),
              ("+", "+  /** rotatedAt set on privilege bump — see AUTH.md s3 */")]},
 ] for i, (fn, _st, desc) in enumerate(FILES)
}

REASONS = [
 "bcrypt cost 10 is dated. argon2id resists GPU cracking; OWASP recommendation.",
 "Sliding refresh keeps mobile sessions alive; rotate on scope bump to kill fixation.",
 "Strict PKCE plus 32-byte nonce closes the implicit-flow gaps.",
 "One middleware attaches the principal and request-id for all handlers.",
 "Cursor pagination stays stable under write load; zod strict on inputs.",
 "Idempotency key makes client retries safe against double charges.",
 "Schema must carry passkey credentials and rotation timestamp.",
 "Migration is backwards-compatible: adds nullable columns, no table lock.",
 "Session namespace v2 with jittered TTL avoids stampedes.",
 "One clear passkey button plus honest error copy.",
 "List every session with a single revoke-all for stolen cookies.",
 "24 new cases pin rotation, replay, and expiry behavior.",
 "Idempotency and cursor contracts locked by tests before frontend ships.",
 "Auth flow docs rewritten around the v2 sequence.",
 "Feature flag gates rollout: 10% canary with auto-rollback on 5xx spike.",
]

ACTIONS = ["approve", "reject-hunk", "regenerate", "explain", "commit-one-op"]
SPINNER = ["-", "\\", "|", "/"]

# ── startup banner (works without curses, pure ANSI) ──────────────────
def splash():
    import shutil
    W = shutil.get_terminal_size((100, 30)).columns
    steps = ["scanning repo", "parsing diffs", "building queue"]
    for f in range(21):
        bar = "#" * f + "-" * (20 - f)
        print("\033[2J\033[H", end="")
        print("\n".join("  " + l for l in LOGO))
        print(("  refactor queue — inspect every hunk, ship once").center(W - 4))
        print("  [%s] %3d%%  %s" % (bar, int(f / 20 * 100), steps[min(f // 7, 2)]))
        time.sleep(0.05)
    print("  ready — launching.")

# ── curses TUI ────────────────────────────────────────────────────────
LIVE_ROOT, LIVE_BRANCH = None, "auth_v2"

class State:
    def __init__(self):
        global FILES, DIFFS, REASONS, LIVE_ROOT, LIVE_BRANCH
        self.pane = 0          # 0 files, 1 diff, 2 detail
        self.fsel = 0
        self.hsel = 0
        self.rsel = 0
        self.scroll = 0
        self.tick = 0
        self.mode = "demo"
        self.committing = False
        self.commit_input = ""
        self.commit_result = ""
        root = git_root()
        if root and "--demo" not in sys.argv:
            live = load_live_queue(root)
            if live:
                FILES, DIFFS, REASONS = live
                LIVE_ROOT, LIVE_BRANCH = root, git_branch(root)
                self.mode = "live"
        n = len(FILES)
        if self.mode == "live":
            self.msg = ("Live repo '%s': %d changed file(s). "
                        "SPACE toggles hunks, C commits." % (LIVE_BRANCH, n))
        else:
            self.msg = ("Demo queue: %d related edits. SPACE toggles hunks, "
                        "x rejects, C commits as one operation. "
                        "Tip: 'patchwork --pick' lists your repos." % n)
        self.committed = False
        self.show_help = False

    @property
    def nfiles(self):
        return len(FILES)

def staged_count():
    n = 0
    for hunks in DIFFS.values():
        n += sum(1 for h in hunks if h["state"])
    return n

def draw(win, st):
    H, W = win.getmaxyx()
    st.tick += 1
    win.erase()
    # header
    nq = len(FILES)
    mode = "LIVE" if st.mode == "live" else "DEMO"
    branch = LIVE_BRANCH if st.mode == "live" else "auth_v2"
    hdr = " PATCHWORK %s  branch: %s  [%s]  %d file(s) " % (
        SPINNER[st.tick % 4], branch, mode, nq)
    try:
        win.addstr(0, 0, " " * (W - 1), curses.color_pair(1))
        win.addstr(0, 1, hdr[:W - 2], curses.color_pair(1) | curses.A_BOLD)
    except curses.error:
        pass
    # layout
    lw, rw = 30, 38
    cw = max(20, W - lw - rw - 4)
    widths = [lw, cw, rw]
    xs = [1, 1 + lw + 1, 1 + lw + 1 + cw + 1]
    titles = ["1 FILES", "2 DIFF", "3 DETAIL"]
    for p in range(3):
        x, wdt = xs[p], widths[p]
        active = (st.pane == p)
        try:
            marker = "> " if active else "  "
            win.addstr(1, x, (marker + titles[p])[:wdt - 1],
                       curses.color_pair(4) | curses.A_BOLD | curses.A_UNDERLINE
                       if active else curses.color_pair(4) | curses.A_BOLD)
            win.vline(2, x + wdt, curses.ACS_VLINE, H - 5)
        except curses.error:
            pass
        if p == 0:
            draw_files(win, st, 2, x, H - 5, wdt)
        elif p == 1:
            draw_diff(win, st, 2, x, H - 5, cw)
        else:
            draw_detail(win, st, 2, x, H - 5, rw)
    # footer
    total = sum(len(h) for h in DIFFS.values())
    staged = staged_count()
    if st.committing:
        try:
            win.addstr(H - 2, 0, " " * (W - 1), curses.color_pair(1))
            win.addstr(H - 2, 1, " Commit message (ENTER ships, ESC cancels):"[:W - 2],
                       curses.color_pair(1) | curses.A_BOLD)
            win.addstr(H - 1, 1, ("> " + st.commit_input + "_")[:W - 2],
                       curses.color_pair(5) | curses.A_BOLD)
        except curses.error:
            pass
        win.refresh()
        return
    pct = int(staged / total * 100) if total else 0
    barw = min(24, W // 5)
    bar = "#" * (pct * barw // 100) + "-" * (barw - pct * barw // 100)
    foot = (" staged %d/%d [%s] %d%% | TAB pane  j/k move  SPC hunk  "
            "a file  x reject  A all  C commit  ? help  q quit " % (staged, total, bar, pct))
    try:
        win.addstr(H - 2, 0, " " * (W - 1), curses.color_pair(2))
        win.addstr(H - 2, 1, foot[:W - 2], curses.color_pair(2))
        win.addstr(H - 1, 1, (">> " + st.msg)[:W - 2], curses.color_pair(3))
    except curses.error:
        pass
    if st.show_help:
        draw_help(win, H, W)
    if st.committed:
        draw_commit(win, H, W, st)
    win.refresh()

def draw_files(win, st, y, x, h, w):
    for i, (fn, flag, _desc) in enumerate(FILES):
        if i < st.scroll or i >= st.scroll + h:
            continue
        r = y + i - st.scroll
        hunks = DIFFS.get(i, [])
        staged = sum(1 for hh in hunks if hh["state"])
        if not hunks or staged == 0:
            mark = "[ ]"
        elif staged == len(hunks):
            mark = "[+]"
        else:
            mark = "[~]"
        sel = (i == st.fsel)
        line = ("%s %s %s" % (mark, flag, fn))[:w - 1]
        if sel:
            attr = curses.color_pair(5) | curses.A_REVERSE | curses.A_BOLD
        elif staged:
            attr = curses.color_pair(3)
        else:
            attr = curses.color_pair(6)
        try:
            win.addstr(r, x, line.ljust(w - 1)[:w - 1], attr)
        except curses.error:
            pass

def draw_diff(win, st, y, x, h, w):
    i = st.fsel
    if i >= len(FILES):
        return
    fn, flag, desc = FILES[i]
    try:
        win.addstr(y, x, (" %s %s" % (flag, fn))[:w - 1],
                   curses.color_pair(5) | curses.A_BOLD)
        win.addstr(y + 1, x, (" " + desc)[:w - 1], curses.color_pair(3))
    except curses.error:
        pass
    r = y + 2
    for hi, hunk in enumerate(DIFFS.get(i, [])):
        if r >= y + h:
            break
        sel = (st.pane == 1 and hi == st.hsel)
        icon = "[+]" if hunk["state"] else "[-]"
        try:
            win.addstr(r, x, ("%s %s" % (icon, hunk["head"]))[:w - 1],
                       (curses.color_pair(5) | curses.A_REVERSE | curses.A_BOLD)
                       if sel else curses.color_pair(4) | curses.A_BOLD)
        except curses.error:
            pass
        r += 1
        for kind, txt in hunk["lines"]:
            if r >= y + h:
                break
            c = 2 if kind == "+" else (7 if kind == "-" else 6)
            attr = curses.color_pair(c)
            if not hunk["state"]:
                attr |= curses.A_DIM
            try:
                win.addstr(r, x, (" " + txt)[:w - 1], attr)
            except curses.error:
                pass
            r += 1
        if not hunk["state"] and r < y + h:
            try:
                win.addstr(r, x, "    (rejected — excluded from commit)"[:w - 1],
                           curses.color_pair(6) | curses.A_DIM)
            except curses.error:
                pass
        r += 1

def draw_detail(win, st, y, x, h, w):
    i = st.fsel
    if i >= len(REASONS):
        return
    try:
        win.addstr(y, x, " Summary:"[:w - 1], curses.color_pair(5) | curses.A_BOLD)
    except curses.error:
        pass
    for j, ln in enumerate(textwrap.wrap(REASONS[i], w - 4)):
        if y + 1 + j >= y + h:
            break
        try:
            win.addstr(y + 1 + j, x + 2, ln, curses.color_pair(3))
        except curses.error:
            pass
    ay = y + 1 + len(textwrap.wrap(REASONS[i], w - 4)) + 1
    try:
        win.addstr(ay, x, " Actions:"[:w - 1], curses.color_pair(4) | curses.A_BOLD)
    except curses.error:
        pass
    for k, a in enumerate(ACTIONS):
        if ay + 1 + k >= y + h:
            break
        sel = (st.pane == 2 and k == st.rsel)
        try:
            win.addstr(ay + 1 + k, x + 2, (("> " if sel else "   ") + "%d. %s" % (k + 1, a))[:w - 3],
                       curses.color_pair(5) | curses.A_REVERSE if sel else curses.color_pair(6))
        except curses.error:
            pass

def draw_help(win, H, W):
    hw, hh = min(60, W - 6), 18
    hx, hy = (W - hw) // 2, (H - hh) // 2
    keys = ["j/k or arrows ...... move selection",
            "TAB / 1/2/3 ........ change pane",
            "SPACE .............. stage / unstage hunk",
            "x .................. reject hunk",
            "a / A .............. approve file / approve all",
            "C .................. commit staged hunks as one commit",
            "? .................. close this help",
            "q .................. quit",
            "",
            "Partially selected files are staged per hunk",
            "via 'git apply --cached'. Rejected hunks stay",
            "in the working tree, out of the commit."]
    try:
        for r in range(hh):
            win.addstr(hy + r, hx, " " * hw, curses.color_pair(2))
        win.addstr(hy, hx + 2, " PATCHWORK help ", curses.color_pair(2) | curses.A_BOLD)
        for j, ln in enumerate(MASCOT.strip("\n").split("\n")):
            if hy + 2 + j < hy + hh - 1:
                win.addstr(hy + 2 + j, hx + 2, ln[:hw - 4], curses.color_pair(2))
        for j, ln in enumerate(keys):
            if hy + 6 + j < hy + hh - 1:
                win.addstr(hy + 6 + j, hx + 2, ln[:hw - 4], curses.color_pair(2))
        win.addstr(hy + hh - 1, hx + 2, " press ? or ESC to close ",
                   curses.color_pair(2) | curses.A_REVERSE)
    except curses.error:
        pass

def draw_commit(win, H, W, st):
    lines = ["COMMIT COMPLETE", "", st.commit_result or "Staged hunks committed as one operation.",
             "", "press any key to continue"]
    hw = max(len(l) for l in lines) + 6
    hh = len(lines) + 2
    hx, hy = max(0, (W - hw) // 2), max(0, (H - hh) // 2)
    try:
        for r in range(hh):
            win.addstr(hy + r, hx, " " * hw, curses.color_pair(1))
        for j, ln in enumerate(lines):
            attr = curses.color_pair(1) | curses.A_BOLD if j == 0 else curses.color_pair(1)
            win.addstr(hy + 1 + j, hx + 3, ln[:hw - 6], attr)
    except curses.error:
        pass

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(250)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # header / dialog
    curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)   # footer / help
    curses.init_pair(3, curses.COLOR_CYAN, -1)                   # info text
    curses.init_pair(4, curses.COLOR_YELLOW, -1)                 # pane titles
    curses.init_pair(5, curses.COLOR_WHITE, -1)                  # selection
    curses.init_pair(6, curses.COLOR_WHITE, -1)                   # dim text
    try:
        curses.init_pair(7, curses.COLOR_RED, -1)
    except curses.error:
        pass
    st = State()
    while True:
        draw(stdscr, st)
        k = stdscr.getch()
        if k == -1:
            continue  # tick for spinner
        if st.committed:
            st.committed = False
            st.msg = "Commit done. Queue cleared for next round."
            continue
        if st.committing:  # commit-message input mode
            if k == 27:
                st.committing = False
                st.msg = "Commit cancelled."
                continue
            if k in (10, 13, curses.KEY_ENTER):
                msg = st.commit_input.strip() or "patchwork: one-op refactor commit"
                st.committing = False
                do_commit(stdscr, st, msg)
                continue
            if k in (curses.KEY_BACKSPACE, 127, 8):
                st.commit_input = st.commit_input[:-1]
                continue
            if 32 <= k < 256:
                st.commit_input += chr(k)[:max(0, 80 - len(st.commit_input))]
            continue
        c = chr(k).lower() if 0 <= k < 256 else ""
        if k in (ord("q"), 27) and not st.show_help:
            break
        if c == "?":
            st.show_help = not st.show_help
            continue
        if st.show_help and k in (27, ord("?"), 13):
            st.show_help = False
            continue
        if k == 9:
            st.pane = (st.pane + 1) % 3
        elif c in ("1", "2", "3"):
            st.pane = int(c) - 1
        elif k in (curses.KEY_UP, ord("k")):
            if st.pane == 0:
                st.fsel = max(0, st.fsel - 1)
            elif st.pane == 1:
                st.hsel = max(0, st.hsel - 1)
            else:
                st.rsel = max(0, st.rsel - 1)
        elif k in (curses.KEY_DOWN, ord("j")):
            if st.pane == 0:
                st.fsel = min(st.nfiles - 1, st.fsel + 1)
            elif st.pane == 1:
                st.hsel = min(len(DIFFS.get(st.fsel, [])) - 1, st.hsel + 1)
            else:
                st.rsel = min(len(ACTIONS) - 1, st.rsel + 1)
        elif k in (curses.KEY_LEFT, ord("h")):
            st.pane = (st.pane - 1) % 3
        elif k in (curses.KEY_RIGHT, ord("l")):
            st.pane = (st.pane + 1) % 3
        elif c == " ":
            h = DIFFS[st.fsel][st.hsel]
            h["state"] = not h["state"]
            st.msg = "%s hunk %d in %s" % (
                "Staged" if h["state"] else "Unstaged", st.hsel + 1, FILES[st.fsel][0])
        elif c == "x":
            DIFFS[st.fsel][st.hsel]["state"] = False
            st.msg = "Rejected hunk %d in %s (excluded from commit)." % (
                st.hsel + 1, FILES[st.fsel][0])
        elif c == "a":
            for hh in DIFFS[st.fsel]:
                hh["state"] = True
            st.msg = "Approved all hunks in %s." % FILES[st.fsel][0]
        elif c == "A":
            for hunks in DIFFS.values():
                for hh in hunks:
                    hh["state"] = True
            st.msg = "Approved all hunks in all files."
        elif c == "c":
            st.committing = True
            st.commit_input = ""
            st.msg = "Type the one-op commit message."
        st.scroll = max(0, min(st.fsel - 2, st.nfiles - (stdscr.getmaxyx()[0] - 5)))
        st.hsel = max(0, min(st.hsel, len(DIFFS.get(st.fsel, [])) - 1))

def do_commit(stdscr, st, msg):
    """Stage selected hunks, commit once. Demo mode is a dry run; live = real git."""
    n = staged_count()
    if st.mode != "live" or not LIVE_ROOT:
        st.commit_result = "Dry run: %d hunks would ship as one commit." % n
        st.msg = "Demo commit recorded (no repo, nothing pushed)."
        draw(stdscr, st)
        time.sleep(0.9)
        st.committed = True
        return
    fails = []
    for i, (fn, _flag, _desc) in enumerate(FILES):
        sel = [h for h in DIFFS[i] if h["state"]]
        if not sel:
            continue
        if len(sel) == len(DIFFS[i]):
            rc, _, err = _git("add", "--", fn, cwd=LIVE_ROOT)
            if rc != 0:
                fails.append("%s: %s" % (fn, err.strip()[:60]))
        else:
            for h in sel:
                ok, err = stage_hunk(LIVE_ROOT, fn, h.get("patch"))
                if not ok:
                    fails.append("%s: %s" % (fn, err[:60]))
                    break
    if fails:
        st.msg = "Stage issues: " + " | ".join(fails)[:100]
        return
    st.msg = "Committing: %s" % msg[:60]
    draw(stdscr, st)
    time.sleep(0.6)
    rc, out, err = git_commit(LIVE_ROOT, msg)
    if rc == 0:
        first = (out.strip().splitlines() or ["done"])[0]
        st.commit_result = "Committed %d hunks: %s" % (n, first[:60])
        st.msg = "Shipped: %s" % first[:90]
        st.committed = True
    else:
        st.msg = "Commit failed: %s" % (err or out).strip()[:110]

def run_tui():
    try:
        curses.wrapper(main)
    except curses.error:
        splash()
        print("  (no TTY detected — banner shown instead. "
              "Run in a real terminal for the 3-pane TUI.)")

if __name__ == "__main__":
    if "--splash" in sys.argv:
        splash()
    elif "--pick" in sys.argv:
        repo = pick_repo()
        if repo:
            if "--print" in sys.argv:
                print(repo)  # for shell helpers: cd "$(patchwork --pick --print)"
            else:
                os.chdir(repo)
                run_tui()
    else:
        run_tui()
