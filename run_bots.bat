@echo off
setlocal EnableExtensions
cd /d "%~dp0"

start "MR Norm VK" /min "%ComSpec%" /c call "%~dp0run_vk_bot.bat"
start "MR Norm Telegram" /min "%ComSpec%" /c call "%~dp0run_telegram_bot.bat"

echo VK and Telegram launch commands have been started.
