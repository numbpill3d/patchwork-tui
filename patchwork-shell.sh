# patchwork shell helper — source this from ~/.bashrc (your choice):
#   source ~/Projects/patchwork-tui/patchwork-shell.sh
#
# Then `pw` lists your git repos and cds your shell into the one you pick.
# (A program cannot cd your shell for you; this wrapper does it.)

pw() {
    local d
    d="$(patchwork --pick --print)" || return 1
    if [ -n "$d" ]; then
        cd "$d" || return 1
    fi
}
