# Bail Agency Outbound Playbook

Research date: April 3, 2026

## Goal

Turn Montana Blotter's existing bail monetization surfaces into live conversations with Montana bail agencies.

Use the product that already exists:

- `Missoula` alert trial: `/bondsman/felony-alerts?county=missoula`
- `Cascade` alert trial: `/bondsman/felony-alerts?county=cascade`
- General ad inventory: `/advertise/bail-bonds`
- Support hub: `/support`

## Commercial order

1. Sell `Missoula` and `Cascade` felony alert trials first.
2. Sell `Yellowstone`, `Gallatin`, and `Flathead` county sponsorship inventory second.
3. Pitch statewide or bundled placements only after a prospect engages.

Reason: the app already has county-specific alert positioning for Missoula and Cascade, so those offers are easier to explain and closer to immediate ROI than a broader brand package.

## De-duplication rules

- `Central Montana Bail Bonds` appears through multiple local microsites. Track it as one network and do not treat each domain as a brand-new company.
- `Big Sky Bail Bonds` has multiple city pages and overlapping contact emails. Treat Helena and Kalispell as one operator unless a rep says otherwise.
- `A+ Montana Bail Bonds`, `AAA Bail Bonds`, and `Bad Boy Bail Bonds` position themselves as statewide operators. Pitch them with county pilot language first, then expand to bundles.

## First outreach list

Use the companion CSV at [`reports/bail_outbound_targets_2026-04-03.csv`](/root/montanablotter/reports/bail_outbound_targets_2026-04-03.csv).

Priority order:

1. `AAA Bail Bonds` for Missoula
2. `Your Bondsman` for Missoula
3. `EZ Bail Bonds` for Cascade / Great Falls
4. `Central Montana Bail Bonds` for Cascade / Great Falls
5. `Western Pawn & Bail` for Yellowstone / Billings
6. `Central Montana Bail Bonds` for Yellowstone / Billings
7. `Big Sky Bail Bonds` for Flathead / Kalispell
8. `Central Montana Bail Bonds` for Gallatin / Bozeman
9. `A+ Montana Bail Bonds` for statewide pilot coverage
10. `Bad Boy Bail Bonds` for statewide pilot coverage

## Offer mapping

### Missoula prospects

Send:

- `/bondsman/felony-alerts?county=missoula&source=outbound_missoula`
- `/advertise/bail-bonds?source=outbound_missoula_followup`

Pitch:

- "You get the lead before the usual call chain catches up."
- "This is a 7-day trial, not a long contract decision."
- "If the alert flow works, we can layer county sponsorship on top."

### Cascade / Great Falls prospects

Send:

- `/bondsman/felony-alerts?county=cascade&source=outbound_cascade`
- `/advertise/bail-bonds?source=outbound_cascade_followup`

Pitch:

- "Cascade is already merchandised as a speed-to-lead county."
- "You are buying faster reaction time, not generic impressions."

### Yellowstone / Gallatin / Flathead prospects

Send:

- `/advertise/bail-bonds?source=outbound_county_inventory`
- `/support?source=outbound_county_inventory`

Pitch:

- "Own the county feed where families are already checking arrests and detention pages."
- "The inventory is limited and county-specific, not a broad untargeted banner buy."

## Contact sequence

### Day 1

- Call first.
- If no answer, use the contact form or public email the same day.
- Send the county-specific page, not the generic homepage.

### Day 3

- Follow up with a short call or text.
- Use a tighter line: "Wanted to see if you want the 7-day trial in Missoula/Cascade before I offer the slot elsewhere."

### Day 7

- Final follow-up.
- Shift from urgency to clarity: "If alerts are not the fit, I can price a county sponsorship instead."

## Phone opener

Use this for Missoula or Cascade:

> I run Montana Blotter. I built a county-specific booking alert that texts your desk when a high-intent booking posts. It is meant to give you a speed-to-lead edge, and I can turn on a 7-day trial in your county if you want to test it.

Use this for Yellowstone, Gallatin, or Flathead:

> I run Montana Blotter. We already have county-level arrest traffic and I opened paid county sponsorship inventory for agencies that want to own the local feed instead of buying broad untargeted ads. I wanted to see if you want first look at your county.

## Email or contact-form copy

Subject:

`7-day [County] booking alert trial for [Agency Name]`

Body:

```text
I run Montana Blotter.

I built a county-specific bail product for Montana agencies that want the lead before the rest of the market reacts.

For [County], the best fit is:
- a 7-day booking alert trial if you want speed-to-lead
- or county sponsorship inventory if you want to own the local feed

Here is the page I would send you:
[offer URL]

If you want, I can turn on a trial or hold the county inventory while we talk.
```

## Operating notes

- Do not open with pricing unless they ask. Open with county advantage and speed-to-lead.
- Do not send the generic support page first. It is a fallback page, not the main sales page.
- If a statewide operator responds, upsell them from one county to a bundle after the first conversation.
- If a network owns multiple microsites, log all activity under one parent brand in your CRM.

## Sources used

- AAA Bail Bonds: https://www.aaabailbondsmissoulamontana.com/about-aaa-bail-bonds/
- Your Bondsman: https://www.yourbondsmanmontana.com/
- Rocky Mountain Bail Bonds: https://www.rockymountainbailbonds.com/
- EZ Bail Bonds: https://www.ezbailbondsmt.com/
- Western Pawn & Bail: https://westernpawnandbail.com/
- A+ Montana Bail Bonds: https://www.montanabailcompany.com/
- Big Sky Bail Bonds Kalispell: https://bigskybail.com/kalispell/
- Big Sky Bail Bonds Helena: https://bigskybail.com/helena/
- Bad Boy Bail Bonds: https://www.badboybailbonds.com/
- Central Montana / local microsites:
  - https://centralmontanabailbonds.com/locations/billings/
  - https://bailbondsbillings.com/
  - https://bailbondsbozeman.com/
  - https://bailbondsgreatfalls.com/
