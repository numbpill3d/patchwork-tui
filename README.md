# PATCHWORK — repo-wide refactor queue TUI

Queue up to 15 related file edits, inspect every hunk, reject what
should not ship, then commit the survivors as one operation.
lazygit-style keybindings, live git integration, stdlib-only (no deps).

## install

```bash
git clone https://github.com/numbpill3d/patchwork-tui.git
cd patchwork-tui
ln -s "$PWD/patchwork.py" ~/bin/patchwork
```

## run

```
patchwork              # live git in a repo, demo queue elsewhere
patchwork --demo       # force demo queue
patchwork --splash     # banner only
```

## keys

`j/k` move · `TAB` cycle pane · `1/2/3` jump pane · `SPACE` stage hunk ·
`x` reject hunk · `a` approve file · `A` approve all · `C` commit one op ·
`?` help · `q` quit

Partially selected files are staged per hunk via `git apply --cached`.
Rejected hunks stay in the working tree, out of the commit.
