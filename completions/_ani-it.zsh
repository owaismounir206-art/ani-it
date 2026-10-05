#compdef ani-it

_ani_it() {
    local -a qualities
    qualities=('1080p:Full HD' '720p:HD' '480p:SD' 'best:Migliore risoluzione disponibile')

    _arguments -s -C \
        '(-c --continue)'{-c,--continue}'[Riprendi l'\''ultimo anime ed episodio visto]' \
        '(-h -H --history)'{-h,-H,--history}'[Mostra la cronologia degli anime con selezione rapida]' \
        '(-d --download)'{-d,--download}'[Attiva la modalità download]' \
        '(-e --episode)'{-e,--episode}'[Numero di episodio o intervallo (es. 1 o 1-12)]:episodio:_values "episodio"' \
        '(-q --quality)'{-q,--quality}'[Forza risoluzione video]:qualità:(1080p 720p 480p best)' \
        '(-t --terminal)'{-t,--terminal}'[Riproduci il video nel terminale]:driver:(tct kitty sixel caca)' \
        '--clear-history[Svuota il database della cronologia locale]' \
        '--clear-cache[Pulisce la cache delle locandine e metadati]' \
        '(-v --version)'{-v,--version}'[Stampa la versione del programma ed esce]' \
        '--help[Mostra il messaggio di aiuto]' \
        '*:anime:->anime_titles'

    case "$state" in
        anime_titles)
            local -a titles
            titles=("${(@f)$(ani-it --list-history-titles 2>/dev/null)}")
            # _describe usa ':' come separatore nome:descrizione: i due punti nei titoli vanno escapati
            titles=("${(@)titles//:/\\:}")
            if (( ${#titles} )); then
                _describe -t titles 'Anime in cronologia' titles
            fi
            ;;
    esac
}

_ani_it "$@"
