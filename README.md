# ani-it 🌸
> **Client CLI/TUI nativo per lo streaming e il download di Anime da AnimeUnity in italiano.**
> Ottimizzato per **Arch Linux**, **Wayland** (Hyprland, Sway), **X11** e **PipeWire**.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Arch Linux](https://img.shields.io/badge/Arch_Linux-Ready-1793d1?logo=arch-linux&logoColor=white)](#-installazione-su-arch-linux)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)](#-dipendenze-di-sistema)

---

## 🌟 Caratteristiche Principali

- **Nativo per Arch Linux**: Progettato per integrarsi con l'ecosistema moderno di Arch (Wayland, PipeWire, socket IPC Unix).
- **Interfaccia TUI**: Selezione interattiva tramite `fzf` con colorazione ANSI, Nerd Fonts v3.x e anteprime dinamiche.
- **Anteprime locandine**: Se `chafa` è installato, la locandina viene mostrata nel pannello di anteprima di `fzf` con caratteri Unicode (`--symbols=vhalf,braille`). Non c'è supporto ai protocolli grafici Kitty/Sixel.
- **Player MPV con socket IPC**: Controllo di `mpv` tramite JSON-IPC, rilevamento della fine episodio (evento `end-file`), **ripresa dalla posizione** in cui avevi interrotto (salvata in cronologia) e avanzamento automatico (`auto_next`).
- **Qualità video**: `-q 720` sceglie la variante HLS giusta del master (`--hls-bitrate` per mpv, filtro d'altezza di `yt-dlp` per i download), senza mai sostituire il master con una singola variante: così non si perdono le tracce audio.
- **Download**: `yt-dlp` per i flussi HLS e `aria2c` per i file diretti, con gli argomenti di `aria2c` configurabili. Intervalli di episodi (`1-12`, `1,3,5`, `all`), anche con episodi decimali (`12.5`), episodio `0` e numerazioni che non partono da 1. I download interrotti si riprendono dal punto raggiunto.
- **Conformità XDG**:
  - Configurazione: `$XDG_CONFIG_HOME/ani-it/config.toml` (o `~/.config/ani-it/config.toml`)
  - Cronologia atomica: `$XDG_STATE_HOME/ani-it/history.json` (o `~/.local/state/ani-it/history.json`)
  - Cache: `$XDG_CACHE_HOME/ani-it/` (o `~/.cache/ani-it/`)
  - Socket runtime: `$XDG_RUNTIME_DIR/ani-it/` (o una directory privata `0700` in `/tmp/ani-it-$UID/`, verificata prima dell'uso)
- **Shell completions** per **Fish**, **Bash** e **Zsh** (i titoli in cronologia sono completati in modo sicuro).
- **Scraper e resolver resilienti**: parser dedicato per gli embed Vixcloud, deoffuscamento di script JS packed (Dean Edwards) e retry esponenziale **solo** sugli errori transitori (rete, 429, 403 e 5xx): un 404 non viene ritentato e viene distinto da "sito irraggiungibile".
- **Connessioni sicure**: la verifica dei certificati TLS è sempre attiva e non c'è nessun downgrade automatico a HTTP.

---

## 📦 Dipendenze di Sistema

### Pacchetti Obbligatori (Arch Linux)
```bash
sudo pacman -S python python-requests python-beautifulsoup4 fzf mpv yt-dlp
```
`fzf` e il player configurato (`player.binary`, di default `mpv`) sono indispensabili: senza, il programma si ferma con un messaggio. `yt-dlp` serve per i download e per gli episodi HLS scaricati: se manca, viene mostrato un avviso e lo streaming continua a funzionare.

### Pacchetti Opzionali Consigliati
- `aria2`: per saturare la banda durante il download multi-connessione.
- `chafa`: per le locandine nella finestra di anteprima di `fzf`.

```bash
sudo pacman -S aria2 chafa
```

---

## 🚀 Installazione su Arch Linux

### Metodo 1: Tramite PKGBUILD (Consigliato)
Il `PKGBUILD` scarica la versione taggata del progetto da GitHub (`v<pkgver>`), la compila, esegue i test (`check()`) e installa `/usr/bin/ani-it` con le completion per Bash, Zsh e Fish:

```bash
git clone https://github.com/owaismounir206-art/ani-it.git
cd ani-it
makepkg -si
```

> Il rilascio deve avere il tag corrispondente (`git tag v1.0.0 && git push --tags`): il `PKGBUILD` lo usa come sorgente riproducibile.

### Metodo 2: Installazione locale con Pip / Virtualenv
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## ⚙️ Configurazione (`config.toml`)

Al primo avvio, `ani-it` genera il file in `$XDG_CONFIG_HOME/ani-it/config.toml`:

```toml
[player]
# Binario del player (controllato all'avvio, non un nome fisso)
binary = "mpv"
# Argomenti addizionali passati a mpv
args = ["--hwdec=auto-safe", "--geometry=1280x720"]

[downloader]
binary = "aria2c"
# Argomenti passati a aria2c, sia per i file diretti sia come downloader esterno di yt-dlp
args = ["-x", "16", "-s", "16", "-k", "1M", "-j", "4"]

[general]
# Dominio di AnimeUnity: cambia spesso, aggiornalo qui se il sito si sposta
base_url = "https://www.animeunity.so"

# Risoluzione preferita: "1080p", "720p", "480p", "best"
quality = "best"

# Mostra le anteprime delle locandine con chafa (richiede chafa installato)
preview_art = true

# Passa automaticamente all'episodio successivo al termine del player
auto_next = false
```

I valori non validi (tipo sbagliato, qualità sconosciuta, `base_url` senza `http://`/`https://`, TOML corrotto) non fermano il programma: vengono sostituiti dal valore predefinito e **segnalati su stderr**.

> Se `mpv.conf` imposta `keep-open=yes`, `mpv` resta aperto a fine episodio. Con `auto_next = true` `ani-it` lo chiude da solo e passa all'episodio successivo.

---

## 📖 Utilizzo

### Ricerca ed Esecuzione Interattiva
```bash
# Cerca un anime e apri il selettore TUI interattivo fzf
ani-it "Chainsaw Man"

# Cerca senza query iniziale (apre direttamente il prompt)
ani-it
```

### Scorciatoie e Modalità Speciali
```bash
# Riprendi l'ultimo anime: dall'episodio interrotto (alla posizione salvata)
# oppure dal successivo se l'ultimo era finito
ani-it -c
ani-it --continue

# Mostra la cronologia degli anime guardati con avanzamento
# (-h è un alias di -H; l'aiuto si ottiene solo con --help)
ani-it -H
ani-it --history

# Modalità download per una serie
ani-it -d "Frieren"

# Seleziona direttamente un episodio (per numero reale; in download anche intervalli)
ani-it -e 5 "Jujutsu Kaisen"
ani-it -d -e 1-12 "Frieren"

# Forza una risoluzione specifica (es. 720p, 1080p, best)
ani-it -q 720 "Attack on Titan"

# Riproduzione video direttamente nel terminale (senza finestra grafica esterna)
ani-it -t "Steins;Gate"         # Driver predefinito 'tct' (True Color Terminal, blocchi Unicode RGB)
ani-it -t kitty "Cyberpunk"     # Grafica pixel Kitty (per Kitty, Ghostty, WezTerm)
ani-it -t sixel "Bocchi"        # Grafica Sixel (per Foot, WezTerm)
ani-it -t caca "Evangelion"     # Modalità ASCII color art

# Pulizia dei dati
ani-it --clear-history   # Azzera la cronologia
ani-it --clear-cache     # Pulisce la cache delle immagini e dei metadati
ani-it --version         # Stampa la versione installata (senza creare file)
ani-it --help            # Mostra l'aiuto
```

### Episodi e cronologia
- `-e N` cerca prima per **numero reale** dell'episodio e solo come ripiego per posizione: con un episodio `0` o numerazioni che non partono da 1 parte l'episodio giusto.
- La cronologia distingue **episodio finito** e **serie finita**: in `--history` una serie risulta "Completato" solo dopo aver finito l'ultimo episodio.
- Se interrompi un episodio, la posizione viene salvata e riprende da lì (`-c` o scegliendo di nuovo lo stesso episodio); con `[r]` riparte dall'inizio.
- Se l'elenco episodi risulta troncato da un errore di rete, il programma lo segnala.

---

## 🎮 Controlli Post-Riproduzione

Al termine della riproduzione di un episodio, appare il menu interattivo. Le voci non disponibili (per esempio "precedente" sul primo episodio) non vengono mostrate né accettate.

| Tasto | Azione |
| :--- | :--- |
| **`[Invio]` / `[n]`** | Riproduci il **prossimo episodio** (all'ultimo, Invio torna alla selezione) |
| **`[p]`** | Riproduci l'**episodio precedente** |
| **`[r]`** | **Riavvia** l'episodio corrente dall'inizio |
| **`[s]`** | Torna alla **selezione degli episodi** |
| **`[d]`** | **Scarica** questo episodio |
| **`[q]`** | **Esci** dal programma |

Se lo stream non si risolve o `mpv` non riesce a riprodurlo, un menu dedicato mostra il motivo (con le ultime righe di log di `mpv`) e permette di **riprovare** (`[Invio]`/`[r]`), cambiare episodio, scaricare o uscire. Dopo un download riuscito si può riprodurre il file scaricato o scaricare il prossimo episodio, senza che parta lo streaming.

---

## 🗑️ Disinstallazione e Rimozione Completa

Se desideri rimuovere `ani-it` dal tuo sistema, segui i passaggi corrispondenti al tuo metodo di installazione:

### 1. Rimozione dell'Eseguibile

#### Se installato tramite pacchetto Arch Linux (`makepkg` o `pacman`):
```bash
sudo pacman -Rns ani-it
```
*(L'opzione `-Rns` rimuove l'eseguibile, le shell completions e tutte le dipendenze non più utilizzate da altri programmi).*

#### Se installato tramite Pip:
```bash
pip uninstall ani-it
```

---

### 2. Rimozione di Dati, Cache e Configurazioni (Opzionale)
`ani-it` rispetta rigorosamente le specifiche **XDG Base Directory**. Per rimuovere completamente tutti i dati locali generati dal programma:

```bash
# 1. Rimuovi le impostazioni personalizzate
rm -rf ~/.config/ani-it

# 2. Rimuovi la cronologia degli anime visti
rm -rf ~/.local/state/ani-it

# 3. Rimuovi la cache locale (locandine scaricate da chafa, sessioni temporanee)
rm -rf ~/.cache/ani-it
```

*(I video scaricati in `~/Downloads/Anime/` non vengono toccati e rimangono al sicuro).*

---

## 🧪 Sviluppo

```bash
# Test (solo libreria standard + requests/bs4; i test shell si saltano se mancano bash-completion, fish o zsh)
python -m unittest discover -s tests

# Lint
pip install ruff
ruff check .
```

La versione è definita **solo** in `ani_it/_version.py`; `pyproject.toml` la legge da lì e `tests/test_packaging.py` verifica che `PKGBUILD` e `.SRCINFO` siano allineati. La CI (GitHub Actions, `.github/workflows/ci.yml`) esegue lint e test su Python 3.12 e 3.13.

---

## 📄 Licenza

Rilasciato sotto licenza [GNU General Public License v3 (GPL-3.0-or-later)](LICENSE).
Realizzato per gli amanti degli anime e del software libero su Arch Linux!
