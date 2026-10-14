# Magyar Posta — still to do

- [ ] Observe a failed delivery or a return, to learn whether those outcomes
      stay inside the 1-5 category ordinal or arrive as a new band-4 detail
      code; until one shows up, `returning` and `problem` are unreachable. An
      unknown band-4 detail code logs a one-shot warning asking for exactly
      that report
- [ ] Find out whether a code-bearing consumer tracking link exists, which is
      what moves `url` out of `PENDING_CAPABILITIES`
- [ ] Watch for throttling under normal polling; none has been observed
- [ ] Outgoing parcels (user-declared per code), as for other account-less
      carriers
- [ ] If you run a cross-carrier aggregator integration, register
      `magyar_posta` with it so its events get picked up
