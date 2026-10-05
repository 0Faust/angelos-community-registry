#!/usr/bin/env fish
set -l root (dirname (status --current-filename))
set -l data_dir $HOME/.local/share/angelos-community-registry-telegram-bot
set -l config_dir $HOME/.config/angelos-community-registry
set -l state_dir $HOME/.local/state/angelos-community-registry
set -l unit_dir $HOME/.config/systemd/user

mkdir -p $data_dir $config_dir $state_dir $unit_dir
or begin
    echo "Could not create bot directories." >&2
    exit 1
end

cp $root/scripts/community-registry-telegram-bot.py $data_dir/community-registry-telegram-bot.py
cp $root/telegram-moderators.json $data_dir/telegram-moderators.json
chmod 755 $data_dir/community-registry-telegram-bot.py

if not test -f $config_dir/telegram-bot.env
    cp $root/telegram-bot.env.example $config_dir/telegram-bot.env
end
chmod 600 $config_dir/telegram-bot.env

cp $root/angelos-community-registry-telegram-bot.service $unit_dir/angelos-community-registry-telegram-bot.service
systemctl --user daemon-reload
or begin
    echo "Could not reload user systemd units." >&2
    exit 1
end

echo "Bot files installed. Edit: $config_dir/telegram-bot.env"
echo "Fill TELEGRAM_BOT_TOKEN and GITHUB_TOKEN, then run:"
echo "systemctl --user enable --now angelos-community-registry-telegram-bot.service"
