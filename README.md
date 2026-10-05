# ani-it 🌸
> **Client CLI/TUI nativo per lo streaming e il download di Anime da AnimeUnity in italiano.**
> Ottimizzato per **Arch Linux**, **Wayland** (Hyprland, Sway), **X11** e **PipeWire**.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Arch Linux](https://img.shields.io/badge/Arch_Linux-Ready-1793d1?logo=arch-linux&logoColor=white)](#installazione-su-arch-linux)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)](#dipendenze)

---

## 🌟 Caratteristiche Principali

- **Nativo per Arch Linux**: Progettato specificamente per integrarsi con l'ecosistema moderno di Arch (Wayland, PipeWire, socket IPC Unix).
- **Interfaccia TUI ad alte prestazioni**: Selezione interattiva tramite `fzf` con colorazione ANSI, Nerd Fonts v3.x e anteprime dinamiche.
- **Anteprime Grafiche Locandine**: Supporto opzionale per locandine nel terminale tramite `chafa` (o protocollo Kitty/Sixel).
- **Player MPV avanzato con Socket IPC**: Controllo in tempo reale di `mpv` tramite socket JSON-IPC, ripristino posizione, rilevamento automatico di EOF / chiusura manuale e riproduzione automatica continua (`auto_next`).
- **Download Multithread Accelerato**: Integrazione con `yt-dlp` e `aria2c` (`-x 16 -s 16 -k 1M -j 4`) con supporto per intervalli di episodi (es. `1-12`).
- **Piena Conformità agli Standard XDG**:
  - Configurazioni: `$XDG_CONFIG_HOME/ani-it/config.toml` (o `~/.config/ani-it/config.toml`)
  - Cronologia atomica: `$XDG_STATE_HOME/ani-it/history.json` (o `~/.local/state/ani-it/history.json`)
  - Cache: `$XDG_CACHE_HOME/ani-it/` (o `~/.cache/ani-it/`)
  - Socket runtime: `$XDG_RUNTIME_DIR/ani-it/` (o `/tmp/ani-it-$UID/`)
- **Shell Completions Native**: Autocompletamento completo e dinamico per **Fish**, **Bash** e **Zsh**.
- **Scraper & Resolver Resiliente**: Bypass intelligente di embed Vixcloud / player proprietari, deoffuscamento di script JS packed (Dean Edwards), e retry esponenziale con gestione 403/503 Cloudflare.

---

## 📦 Dipendenze di Sistema

### Pacchetti Obbligatori (Arch Linux)
```bash
sudo pacman -S python python-requests python-beautifulsoup4 fzf mpv yt-dlp
```

### Pacchetti Opzionali Consigliati
- `aria2`: per saturare la banda durante il download multi-connessione.
- `chafa`: per il rendering a caratteri/grafico delle locandine nella finestra di preview di `fzf`.

```bash
sudo pacman -S aria2 chafa
```

---

## 🚀 Installazione su Arch Linux

### Metodo 1: Tramite PKGBUILD (Consigliato)
Clona o copia la directory del progetto ed esegui `makepkg`:

```bash
git clone https://github.com/owaismounir206-art/ani-it.git
cd ani-it
makepkg -si
```

Il `PKGBUILD` si occuperà automaticamente di compilare il pacchetto Python, installare l'eseguibile di sistema `/usr/bin/ani-it` e registrare i file di completamento per Bash, Zsh e Fish.

### Metodo 2: Installazione locale con Pip / Virtualenv
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## ⚙️ Configurazione (`config.toml`)

Al primo avvio, `ani-it` genera automaticamente il file di configurazione in `$XDG_CONFIG_HOME/ani-it/config.toml`:

```toml
# ==============================================================================
# File di configurazione per ani-it
# Percorso: ~/.config/ani-it/config.toml
# ==============================================================================

[player]
binary = "mpv"
# Argomenti addizionali passati a mpv
args = ["--hwdec=auto-safe", "--geometry=1280x720"]

[downloader]
binary = "aria2c"
# Connessioni simultanee per aria2c
args = ["-x", "16", "-s", "16", "-k", "1M", "-j", "4"]

[general]
# Priorità risoluzione: "1080p", "720p", "480p", "best"
quality = "best"

# Lingua predefinita ("it" per doppiaggio italiano o "sub-it" per sottotitoli)
language = "it"

# Mostra le anteprime delle locandine con chafa (richiede chafa installato)
preview_art = true

# Passa automaticamente all'episodio successivo al termine del player
auto_next = false
```

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
# Riprendi immediatamente l'ultimo anime ed episodio visto
ani-it -c
ani-it --continue

# Mostra la cronologia degli anime guardati con avanzamento
ani-it -h
ani-it --history

# Modalità download per una serie
ani-it -d "Frieren"

# Seleziona direttamente un episodio saltando il menu di selezione
ani-it -e 5 "Jujutsu Kaisen"

# Forza una risoluzione specifica (es. 720p, 1080p, best)
ani-it -q 720 "Attack on Titan"

# Pulizia dei dati
ani-it --clear-history   # Azzera la cronologia
ani-it --clear-cache     # Pulisce la cache delle immagini e sessioni
ani-it --version         # Stampa la versione installata
```

---

## 🎮 Controlli Post-Riproduzione

Al termine della riproduzione di un episodio in `mpv`, apparirà immediatamente il menu interattivo:

| Tasto | Azione |
| :--- | :--- |
| **`[Invio]` / `[n]`** | Riproduci il **prossimo episodio** |
| **`[p]`** | Riproduci l'**episodio precedente** |
| **`[r]`** | **Riavvia** l'episodio corrente |
| **`[s]`** | Torna alla **selezione degli episodi** |
| **`[d]`** | **Scarica** questo episodio |
| **`[q]`** | **Esci** dal programma |

---

## 🧪 Esecuzione dei Test

Per eseguire la suite di unit test:

```bash
python -m unittest discover -s tests
```

---

## 📄 Licenza

Rilasciato sotto licenza [GNU General Public License v3 (GPL-3.0)](LICENSE).
Realizzato per gli amanti degli anime e del software libero su Arch Linux!
