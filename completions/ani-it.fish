# Fish completion for ani-it

function __ani_it_history_titles
    ani-it --list-history-titles 2>/dev/null
end

# Opzioni generali
complete -c ani-it -s c -l continue -d "Riprendi l'ultimo anime ed episodio riprodotto"
complete -c ani-it -s h -s H -l history -d "Mostra la cronologia degli anime visti"
complete -c ani-it -s d -l download -d "Attiva la modalità download con yt-dlp e aria2c"
complete -c ani-it -s e -l episode -d "Numero di episodio o intervallo (es. 1 o 1-12)" -r
complete -c ani-it -s q -l quality -d "Forza risoluzione video" -x -a "1080p 720p 480p best"
complete -c ani-it -s t -l terminal -d "Riproduci il video direttamente nel terminale" -x -a "tct kitty sixel caca"
complete -c ani-it -l clear-history -d "Svuota il database della cronologia locale"
complete -c ani-it -l clear-cache -d "Pulisce la cache delle locandine e metadati"
complete -c ani-it -s v -l version -d "Mostra la versione del programma"
complete -c ani-it -l help -d "Mostra il messaggio di aiuto"

# Completamento dinamico con i titoli degli anime visti
complete -c ani-it -f -a "(__ani_it_history_titles)" -d "Anime in cronologia"
