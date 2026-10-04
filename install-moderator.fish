#!/usr/bin/env fish
# Install the registry moderator as a command available from any directory.

set -l source_dir (dirname (status --current-filename))
set -l app_dir $HOME/.local/share/angelos-community-registry-moderator
set -l bin_dir $HOME/.local/bin
set -l moderator_source $source_dir/scripts/community-registry-moderator.py
set -l gh_source /tmp/gh-cli-2.102.0/gh_2.102.0_linux_amd64/bin/gh
set -l downloaded_source ""

printf '\n%s\n' \
    '████████████████████████████████████████████████████████████████████████' \
    '██                                                                  ██' \
    '██                 ТОЛЬКО ДЛЯ МОДЕРАТОРОВ                          ██' \
    '██                                                                  ██' \
    '████████████████████████████████████████████████████████████████████████' \
    ''

if not test -f $moderator_source
    set moderator_source https://raw.githubusercontent.com/futureUnd1ground/angelos-community-registry/main/scripts/community-registry-moderator.py
    if not command -q curl
        echo "curl is required to download the moderator." >&2
        exit 1
    end
    set downloaded_source (mktemp)
    or begin
        echo "Could not create a temporary download file." >&2
        exit 1
    end
    curl -fsSL $moderator_source -o $downloaded_source
    or begin
        rm -f -- $downloaded_source
        echo "Could not download the moderator from GitHub." >&2
        exit 1
    end
    set moderator_source $downloaded_source
end

mkdir -p $app_dir $bin_dir
or begin
    echo "Could not create user install directories." >&2
    exit 1
end

cp $moderator_source $app_dir/community-registry-moderator.py
or begin
    test -n "$downloaded_source"; and rm -f -- $downloaded_source
    echo "Could not install the moderator script." >&2
    exit 1
end
test -n "$downloaded_source"; and rm -f -- $downloaded_source

if not command -q gh; and test -x $gh_source; and not test -e $bin_dir/gh
    cp $gh_source $bin_dir/gh
    or begin
        echo "Could not install the existing GitHub CLI." >&2
        exit 1
    end
    chmod 755 $bin_dir/gh
end

printf '%s\n' '#!/bin/sh' 'exec python3 "$HOME/.local/share/angelos-community-registry-moderator/community-registry-moderator.py" "$@"' > $bin_dir/community-registry-moderator
or begin
    echo "Could not create the moderator command." >&2
    exit 1
end
chmod 755 $bin_dir/community-registry-moderator

if command -q fish_add_path
    fish_add_path $bin_dir
end

echo "Installed command: community-registry-moderator"
if not command -q gh
    echo "GitHub CLI is missing. Install it, then run: gh auth login" >&2
else if gh auth status >/dev/null 2>&1
    echo "GitHub CLI is authenticated."
else
    echo "Before moderating, authenticate this Fish environment with: gh auth login" >&2
end

echo "Run community-registry-moderator from any directory."
