#!/bin/bash
# Run all Australian states sequentially overnight
# Resumes automatically if interrupted — just re-run this script

cd "$(dirname "$0")"
source ../venv/bin/activate

LOG="all_states_run.log"
echo "======================================" | tee -a $LOG
echo "Started: $(date)" | tee -a $LOG
echo "======================================" | tee -a $LOG

# NSW — resume from where it left off (progress already saved)
python3 scraper_state.py \
  --state NSW --pc-start 2000 --pc-end 2999 \
  --canary-suburb Sydney --canary-pc 2000 2>&1 | tee -a $LOG

# QLD
python3 scraper_state.py \
  --state QLD --pc-start 4000 --pc-end 4999 \
  --canary-suburb Brisbane --canary-pc 4000 2>&1 | tee -a $LOG

# SA
python3 scraper_state.py \
  --state SA --pc-start 5000 --pc-end 5999 \
  --canary-suburb Adelaide --canary-pc 5000 2>&1 | tee -a $LOG

# WA
python3 scraper_state.py \
  --state WA --pc-start 6000 --pc-end 6999 \
  --canary-suburb Perth --canary-pc 6000 2>&1 | tee -a $LOG

# TAS
python3 scraper_state.py \
  --state TAS --pc-start 7000 --pc-end 7999 \
  --canary-suburb Hobart --canary-pc 7000 2>&1 | tee -a $LOG

# NT
python3 scraper_state.py \
  --state NT --pc-start 800 --pc-end 999 \
  --canary-suburb Darwin --canary-pc 800 2>&1 | tee -a $LOG

echo "======================================" | tee -a $LOG
echo "ALL STATES DONE: $(date)" | tee -a $LOG
echo "======================================" | tee -a $LOG
