# ani-it 🌸
> **Client CLI/TUI nativo per lo streaming e il download di Anime da AnimeUnity in italiano.**
> Hardened, ultraleggero (<30 MB RAM) e multipiattaforma (**Linux**, **macOS**, **Windows**).

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Arch Linux](https://img.shields.io/badge/Arch_Linux-Ready-1793d1?logo=arch-linux&logoColor=white)](#-installazione-su-arch-linux)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)](#-dipendenze-di-sistema)

---

## 🌟 Caratteristiche Principali (v2.0)

- **Hardened & Sicuro**: Prevenzione totale di command/argument injection (nessun `shell=True`), sanitizzazione rigorosa dei file e nomi directory, validazione rigida degli URL e protocolli.
- **Zero-Overhead Scraping**: BeautifulSoup4 completamente rimosso in favore di regex compilate statiche ad alte prestazioni e decoding JSON diretto; consumo di memoria ridotto sotto i 30 MB di baseline RSS.
- **Multipiattaforma Nativo**: Supporto completo per **Linux** (qualsiasi distro/XDG), **macOS** (Application Support/Caches) e **Windows 10/11** (Named Pipes IPC, ANSI processing abilitato).
- **Interfaccia TUI**: Selezione interattiva tramite `fzf` con colorazione ANSI (palette Tokyo Night), Nerd Fonts v3.x e anteprime locandine con chafa (con cache LRU su disco fino a 15 elementi).
- **Player MPV con socket IPC**: Controllo di `mpv` tramite JSON-IPC (socket Unix o Named Pipe su Windows), rilevamento fine episodio (`end-file`), ripresa automatica della posizione salvata e buffer di rete hard-capped per prevenire memory leak.
- **Qualità video e Streaming**: Selezione automatica o manuale della qualità (`1080p`, `720p`, `480p`, `best`), fallback controllato a porta 80 per CDN statici privi di listener SSL.
- **Download Resiliente**: Download chunked a blocchi da 64 KB (senza buffering cumulativo in RAM), `--disk-cache=16M` con `aria2c` e `yt-dlp`. Intervalli di episodi flessibili (`1-12`, `1,3,5`, `all`) e ripresa automatica dei download interrotti.
- **Shell Completions** per **Fish**, **Bash** e **Zsh**.

---

## 📦 Dipendenze di Sistema

### Pacchetti Obbligatori (Arch Linux)
```bash
sudo pacman -S python python-requests fzf mpv yt-dlp
```
`fzf` e il player configurato (`player.binary`, di default `mpv`) sono indispensabili: senza, il programma si ferma con un messaggio. `yt-dlp` serve per i download e per gli episodi HLS scaricati: se manca, viene mostrato un avviso e lo streaming continua a funzionare.

### Pacchetti Opzionali Consigliati
- `aria2`: per accelerare i download con connessioni segmentate multi-thread.
- `chafa`: per le locandine grafiche nella finestra di anteprima di `fzf`.

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

> Il rilascio deve avere il tag corrispondente (`git tag v2.0.0 && git push --tags`): il `PKGBUILD` lo usa come sorgente riproducibile.

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
# Test (solo libreria standard + requests; i test shell si saltano se mancano bash-completion, fish o zsh)
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
