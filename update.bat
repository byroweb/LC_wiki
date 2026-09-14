@echo off
rem Rebuild everything after new game data: pull the content, then regenerate
rem the wiki, map tiles and the data tables (site\data\tables\*.json) from it.
rem
rem   update.bat            pull + full rebuild (tiles re-rendered, ~1 min)
rem   update.bat --no-pull  rebuild only
rem
rem Item/NPC icons are rendered from the client's 3D models and only change when models
rem change; regenerate them with:  (needs bun)
rem   cd source\engine && bun install && bun run tools\pack\Build.ts
rem   cd ..\client && bun install && bun run ..\..\build\icons.ts && bun run ..\..\build\heads.ts
setlocal
cd /d "%~dp0"
set CONTENT=source\content
if not "%1"=="--no-pull" (
  echo Pulling %CONTENT% ...
  git -C "%CONTENT%" pull --ff-only
)
python build\build.py --content "%CONTENT%" --force-tiles
if errorlevel 1 (
  echo BUILD FAILED
  exit /b 1
)
echo Done. Tables: site\data\tables\   Wiki: site\index.html
