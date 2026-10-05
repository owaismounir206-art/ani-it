# Maintainer: ani-it contributors <info@ani-it.org>
pkgname=ani-it
pkgver=1.0.0
pkgrel=1
pkgdesc="CLI interattiva in stile ani-cli per lo streaming di anime da AnimeUnity in italiano"
arch=('any')
url="https://github.com/owaismounir206-art/ani-it"
license=('GPL-3.0-or-later')
depends=(
    'python'
    'python-requests'
    'python-beautifulsoup4'
    'fzf'
    'mpv'
    'yt-dlp'
)
optdepends=(
    'aria2: per accelerare i download con connessioni multiple segmentate'
    'chafa: per visualizzare le anteprime delle locandine nella TUI'
)
makedepends=(
    'python-build'
    'python-installer'
    'python-wheel'
    'python-setuptools'
)
source=()
sha256sums=()

build() {
    cd "$startdir"
    python -m build --wheel --no-isolation
}

package() {
    cd "$startdir"
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Installazione delle shell completions
    install -Dm644 completions/ani-it.fish "$pkgdir/usr/share/fish/vendor_completions.d/ani-it.fish"
    install -Dm644 completions/ani-it.bash "$pkgdir/usr/share/bash-completion/completions/ani-it"
    install -Dm644 completions/_ani-it.zsh "$pkgdir/usr/share/zsh/site-functions/_ani-it"

    # Installazione della licenza
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
