# Strategy

## The problem

Predict the 5 largest new pump.fun coins by market cap at seven answer
reads, 7 to 13 hours after the history ends. Two facts from the practice
history drive everything:

1. **Churn is extreme.** The top 20 at one 10-minute mark shares only 2
   coins with the top 20 thirty minutes later. The coins on top are 5 to
   48 minutes old; the whole field is a flood of newborns.
2. **Newcomers take places.** At the practice's last read, 2 of the top 5
   were launched after the history ended, and 3 of the 9 coins that held a
   top-5 place at any read were. At a 7 to 13 hour horizon the newcomer
   share is much larger, because a coin that is peaking at the cut is
   7 to 13 hours past its peak by the reads.

A persistence forecast (the house starter) is therefore the wrong shape
for the final: it bets on the coins that are biggest now, which is exactly
what the churn kills.

## The model (`main.py`, Python stdlib only, deterministic)

1. **Lifecycle from the history itself.** For every mint, build its log10
   market-cap series over the marks and estimate: the peak age (median age
   at a coin's max cap, clamped 0.5 to 3 h), the rise and decay slopes
   from each coin's recent window (classified by age against the peak age;
   a weak measured decay is not trusted, floor -0.10 log10 per hour), the
   completion level (median cap at the first mark a coin is complete,
   about 830 SOL in the practice), the completion rate of near-complete
   curve coins, the dispersion of log-cap changes at each lag, and the cap
   distribution of young top coins (the newcomers' caps).
2. **Candidate projection.** Top 100 coins by cap at the cut plus the top
   25 recent growers (at most 120). Each is projected to every read
   horizon with a lifecycle mean (rise while younger than the peak age,
   then decay; the expected peak is capped near 10x the completion level)
   and a horizon dispersion (the lag-dispersion table, extrapolated like
   sqrt(h) past the measured span, plus the coin's own volatility). A
   curve coin may graduate in the sample and jump to the completion level.
3. **Newcomer competition.** Each Monte Carlo sample (3,000 per horizon,
   fixed seed) also draws the top newcomer caps at that read from the
   young-top distribution and ranks them with the candidates. A newcomer
   that beats the candidates takes the place, and that sample contributes
   nothing to any named row at that place. So a place's named share is
   exactly the chance a coin from the history holds it, and the rest stays
   unnamed, which is what scores a coin born after the cut.
4. **Emission.** Place probabilities are the Monte Carlo frequencies
   averaged over the seven read horizons, rows with p below 0.0002 dropped
   (up to 100 rows per place, so the unnamed share concentrates on few
   chart coins). Sizes are the Monte Carlo caps at each place, trimmed to
   a 0.93 sum. `top5` ranks candidates by expected place score.

## Offline validation (no local grade exists for program/v1)

I reimplemented the exact scorer from the kit's `grader/forecast/ranked.ts`
and backtested on the practice history: cut at each mark, forecast the
next mark, score against it. Mean over 4 cuts: mine **-33.72**, house
starter **-37.16**, even spread **-45.06**. Mine wins or ties on every
cut, and beats the even spread by 11 nats on average, so the score is not
one window's luck. A pseudo-final run (reads moved to 7 to 13 hours out)
shows the intended long-horizon shape: place 1's named share falls to
0.22 (newcomers take 78%), places 2 to 5 keep 0.40 to 0.73, and the size
modes decline smoothly from about 5,000 SOL at place 1 to about 1,400 SOL
at place 5. Two runs on the same input are byte-identical.

## What would beat this

- A better newcomer cap model (the young-top prior is the weakest part:
  it is estimated from a 1-hour practice history, and the final's 11-hour
  history will calibrate it far better).
- A per-theme momentum term (AI-name coins cluster in the practice top 5,
  but naming a theme by hand is exactly the kind of overfit that does not
  survive a new window, so it is deliberately not in the model).
