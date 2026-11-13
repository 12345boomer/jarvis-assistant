#!/bin/zsh
# Claude Remote: keeps a Claude Code Remote Control server running so Ben can
# start new Claude Code sessions from the Claude app on his phone.
# Opened at login by a macOS Login Item (install_claude_remote.sh).
# Runs in a Terminal window on purpose: Terminal already has access to ~/Documents,
# which launchd jobs don't. Close the window to stop it until next login.

export PATH="$HOME/.local/bin:$HOME/.homebrew/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
printf '\e]0;Claude Remote (leave open)\a'
cd "$HOME/Documents" || exit 1

while true; do
  claude remote-control
  echo ""
  echo "Claude Remote stopped (exit $?). Restarting in 15 seconds. Close this window to stop it."
  sleep 15
done
