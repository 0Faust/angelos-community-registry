#!/usr/bin/env fish
set -l root (dirname (status --current-filename))
set -l data_dir $HOME/.local/share/angelos-community-registry
set -l bin_dir $HOME/.local/bin
set -l app_dir $HOME/.local/share/applications
mkdir -p $data_dir $bin_dir $app_dir
cp $root/scripts/community-registry-gui.py $data_dir/community-registry-gui.py
chmod 755 $data_dir/community-registry-gui.py
printf '%s\n' '#!/bin/sh' 'exec python3 "$HOME/.local/share/angelos-community-registry/community-registry-gui.py" "$@"' > $bin_dir/community-registry-gui
chmod 755 $bin_dir/community-registry-gui
printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=AngelOS Community Registry' 'Comment=Moderate community plugin submissions' "Exec=$HOME/.local/bin/community-registry-gui" 'Icon=system-software-install' 'Terminal=false' 'Categories=System;Settings;' > $app_dir/angelos-community-registry.desktop
echo 'Installed: community-registry-gui'
echo 'Launch from your app menu or run: community-registry-gui'
