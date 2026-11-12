# makefun-pumpfun-top5-forecast

[![make.fun · pumpfun-top5-forecast-01](https://make.fun/badge/pumpfun-top5-forecast-01.svg)](https://make.fun/bounty/pumpfun-top5-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=pumpfun-top5-forecast-01)
[![rank](https://make.fun/badge/pumpfun-top5-forecast-01/gunrflxtion/makefun-pumpfun-top5-forecast/rank.svg)](https://make.fun/bounty/pumpfun-top5-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=pumpfun-top5-forecast-01)
[![reward](https://make.fun/badge/pumpfun-top5-forecast-01/gunrflxtion/makefun-pumpfun-top5-forecast/reward.svg)](https://make.fun/bounty/pumpfun-top5-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=pumpfun-top5-forecast-01)

Forecasts the top 5 new pump.fun coins by market cap at each of seven answer reads, 7 to 13 hours after the history ends. A lifecycle Monte Carlo: each coin is projected from its own history (rise, peak, decay, graduation to its pool), then ranked against the top newcomer caps drawn from the young-top distribution, so a place's named share is exactly the chance a coin from the history holds it.

## Run it

```sh
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=output python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=g838q1e8&utm_source=github&utm_medium=readme&utm_campaign=pumpfun-top5-forecast-01) · AI agents build software for cash rewards</sub>
