# Maintainer: ani-it contributors <info@ani-it.org>
pkgname=ani-it
pkgver=2.0.0   # deve coincidere con ani_it/_version.py (verificato da tests/test_packaging.py)
pkgrel=1
pkgdesc="CLI interattiva in stile ani-cli per lo streaming di anime da AnimeUnity in italiano"
arch=('any')
url="https://github.com/owaismounir206-art/ani-it"
license=('GPL-3.0-or-later')
depends=(
    'python'
    'python-requests'
    'fzf'
    'mpv'
    'yt-dlp'
)
optdepends=(
    'aria2: per accelerare i download con connessioni multiple segmentate'
    'chafa: per visualizzare le anteprime delle locandine nella TUI'
)
makedepends=(
    'git'
    'python-build'
    'python-installer'
    'python-wheel'
    'python-setuptools'
)
# Sorgente riproducibile: il tag di rilascio v$pkgver (git tag v1.0.0 && git push --tags).
# Le sorgenti git non hanno checksum (SKIP): l'integrità è garantita dal tag.
source=("$pkgname::git+$url.git#tag=v$pkgver")
sha256sums=('SKIP')

build() {
    cd "$srcdir/$pkgname"
    python -m build --wheel --no-isolation
}

check() {
    cd "$srcdir/$pkgname"
    python -m unittest discover -s tests
}

package() {
    cd "$srcdir/$pkgname"
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Installazione delle shell completions
    install -Dm644 completions/ani-it.fish "$pkgdir/usr/share/fish/vendor_completions.d/ani-it.fish"
    install -Dm644 completions/ani-it.bash "$pkgdir/usr/share/bash-completion/completions/ani-it"
    install -Dm644 completions/_ani-it.zsh "$pkgdir/usr/share/zsh/site-functions/_ani-it"

    # Installazione della licenza
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
