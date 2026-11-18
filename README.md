# Twitch launch-day audience forecast

[![make.fun bounty](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)

This entry estimates five quantiles for each of the 23 hours after the first hour of a game's UTC launch day. It uses the audience observed before the cutoff and the historical launches bundled with the case.

The forecaster weights historical launches by platform, rollout and similarity to the target's opening audience. A regularized local regression adjusts the comparable curves on the log scale. The adjusted curves form the forecast distribution. When minute-level readings are available within the first hour, a short-lived correction accounts for the direction of the audience at the cutoff.

The program uses Python 3.13 and the standard library. It reads `/input/*.in` and writes `/output/*.out`. Set `MAKEFUN_INPUT_DIR` and `MAKEFUN_OUTPUT_DIR` to run it locally on the official kit's inputs.

```sh
MAKEFUN_INPUT_DIR=/path/to/kit/input MAKEFUN_OUTPUT_DIR=./out python3 -P -s main.py
```
