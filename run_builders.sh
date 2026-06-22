#!/usr/bin/env bash

nohup taskset -c 0-9,12-15 \
  python3 DatasetBuilder/build_hdf5_batch.py \
  --demos-root runtime_sessions/demos \
  > ./DatasetBuilder/batch.log 2>&1 &

pid1=$!

nohup python3 DatasetBuilder/build_archive_batch.py \
  --demos-root runtime_sessions/demos \
  > ./DatasetBuilder/batch1.log 2>&1 &

pid2=$!

echo "build_hdf5_batch.py successfully started, PID: $pid1"
echo "build_archive_batch.py successfully started, PID: $pid2"