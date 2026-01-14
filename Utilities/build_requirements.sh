grep -h "^import\|^from" *.py | awk '{print $2}' | cut -d. -f1 | sort -u > requirements.txt
