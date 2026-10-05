#!/usr/bin/env bash
# Bash completion script for ani-it

_ani_it_completions() {
    local cur prev words cword
    _init_completion || return

    local opts="-c --continue -h -H --history -d --download -e --episode -q --quality --clear-history --clear-cache -v --version --help"

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

    # Completamento con i titoli in cronologia. I titoli arrivano dall'API del sito: non vanno
    # mai passati a `compgen -W`, che espande $(...) ed elimina gli apici (esecuzione di comandi).
    # Confronto per prefisso e quoting con %q.
    local title
    while IFS= read -r title; do
        [[ -n "${title}" && "${title}" == "${cur}"* ]] && COMPREPLY+=( "$(printf '%q' "${title}")" )
    done < <(ani-it --list-history-titles 2>/dev/null)
}

complete -F _ani_it_completions ani-it
