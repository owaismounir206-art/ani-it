#!/usr/bin/env bash
# Bash completion script for ani-it

_ani_it_completions() {
    local cur prev words cword
    _init_completion || return

    local opts="-c --continue -H --history -d --download -e --episode -q --quality --clear-history --clear-cache -v --version -h --help"

    case "${prev}" in
        -q|--quality)
            COMPREPLY=( $(compgen -W "1080p 720p 480p best" -- "${cur}") )
            return 0
            ;;
        -e|--episode)
            return 0
            ;;
    esac

    if [[ "${cur}" == -* ]]; then
        COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
        return 0
    fi

    # Completamento con i titoli in cronologia
    local titles
    titles=$(ani-it --list-history-titles 2>/dev/null)
    if [[ -n "${titles}" ]]; then
        local IFS=$'\n'
        COMPREPLY=( $(compgen -W "${titles}" -- "${cur}") )
    fi
}

complete -F _ani_it_completions ani-it
