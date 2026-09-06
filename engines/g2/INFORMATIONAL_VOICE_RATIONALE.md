# Informational short-video voice rationale

## Goal

G2 should help a viewer follow and retain one coherent operational argument. It
does not optimize narration for constant excitement, maximum speech speed, or
isolated engagement signals.

## Findings applied in v0.7.2

- A 2024 multimedia-learning experiment found that vocal enthusiasm and visual
  cueing each improved retention independently, but their benefits were not
  cumulative when combined. G2 therefore uses selective voice energy while its
  imagery remains contextual and restrained.
- A 2024 experiment on storytelling-narrated video reported stronger retention
  and knowledge transfer than lecture-style narration. G2 preserves a complete
  tension, consequence and resolution arc across scenes.
- A 2024 video-learning study found tone of voice important for guiding
  attention, while relevant visual cues were stronger for retention and
  irrelevant cues could make viewers miss information. G2 does not add visual
  decoration merely to mirror every vocal emphasis.
- A 2026 fMRI experiment found poorer recall after fragmented short-video
  exposure than after a continuous narrative matched for duration and content.
  G2 scenes therefore remain one semantic sequence, not six independent hooks.
- An observational study of 901 TikTok health-information videos found speech
  rate, spectral centroid and expression professionalism associated with viewer
  engagement. This supports testing pace and vocal brightness, but it does not
  establish comprehension or causal effects; G2 treats it as a secondary signal.

## Render measurements

Five v0.7.1 Edge renders measured approximately -16.0 to -16.6 LUFS integrated,
with only 1.8 to 3.2 LU of loudness range. The mastering level was appropriate,
but the narrow dynamic range supported the listening impression of clean yet
flat delivery.

Version 0.7.2 retains an approximately -16 LUFS program level while assigning
small purpose-specific targets:

- cover: restrained tension;
- evidence and explanation: clearer, slightly faster delivery;
- failure point: slower and quieter consequence;
- control system: the strongest, most confident resolution;
- CTA: warm deceleration.

## Sources

- https://doi.org/10.1111/jcal.13049
- https://doi.org/10.1016/j.compedu.2024.105062
- https://doi.org/10.1177/21582440241271267
- https://doi.org/10.1038/s41539-025-00399-y
- https://doi.org/10.1108/IMDS-04-2024-0385
