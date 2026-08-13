@echo off
cd /d "%~dp0"
echo Запуск регистрации по всем Outlook профилям...
echo Остановка: Ctrl+C один раз (мягко) или два раза (жёстко)
echo.
node run_all_profiles.js %*
echo.
echo Готово. Нажми любую клавишу чтобы закрыть...
pause >nul
