#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
"$SCRIPT_DIR/scripts/praattypen" run
status=$?

echo
if [[ $status -eq 0 ]]; then
  echo "PraatTypen is gestopt."
else
  echo "PraatTypen stopte met foutcode $status."
fi
echo "Druk op Enter om dit venster te sluiten."
read -r _
exit $status
