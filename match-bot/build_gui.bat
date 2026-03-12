@echo off
REM Build Match-Bot GUI as a standalone Windows executable.
REM Requires: pip install pyinstaller

pyinstaller --onedir --windowed ^
  --name "Match-Bot" ^
  --hidden-import jellyfish ^
  --hidden-import scipy.optimize ^
  --hidden-import yaml ^
  --hidden-import match_bot.core ^
  --hidden-import match_bot.core.config ^
  --hidden-import match_bot.core.data_loader ^
  --hidden-import match_bot.core.fuzzy ^
  --hidden-import match_bot.core.lookup ^
  --hidden-import match_bot.core.matching ^
  --hidden-import match_bot.core.reporting ^
  --hidden-import match_bot.core.standardization ^
  --hidden-import match_bot.scripts.run_matching ^
  --hidden-import match_bot.scripts.generate_lookups ^
  --hidden-import match_bot.scripts.suggest_matches ^
  match_bot/gui/app.py

echo.
echo Build complete. Output in dist/Match-Bot/
pause
