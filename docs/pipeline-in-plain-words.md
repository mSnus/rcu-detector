# How the remote identifier works, in plain words

What happens between someone photographing a remote control and the system
saying "this is a Sony RM-PJ20".

Times are scaled so **the whole search takes 10 seconds**. They come from a
real measurement, not a guess — see [Where the numbers come from](#where-the-numbers-come-from)
at the end.

---

## Part A — turning a photograph into a "fingerprint"

The system never stores or compares photographs. It reduces every remote to a
description: where the buttons are, how big, what shape, what colour, what is
printed on them. That description is called a **fingerprint**, and the same
code produces it for a catalogue photo and for a user's upload — deliberately,
so the two can never drift apart.

| # | Stage | In plain words | Time |
|---|---|---|---|
| 1 | **Decode the file** | Turn the uploaded bytes into a picture. A damaged JPEG is repaired first, because otherwise the missing part of the picture is whatever junk happened to be in memory — and the same file would read differently on two attempts. | 0.004 s |
| 2 | **Size check** | Too small → refused. The **long side** must be at least 600 pixels (not the height: a remote is long and thin, and the catalogue's own standard is about 300 × 1090). Too big → shrunk, not refused; a 48-megapixel phone photo is perfectly legitimate. | instant |
| 3 | **Find the remotes** | Separate remote from background. Two different methods run and the more believable answer wins: one copes with grey gradient backdrops, the other keeps two remotes apart when a watermark is stamped across both. This step can find several remotes in one photo. | 0.03 s |
| 4 | **Cut out and straighten** | Each remote is cut from the photo, rotated upright, and **scaled to a standard 400 pixels wide**. From here on, every remote is the same size regardless of the photo it came from. | 0.02 s |
| 5 | **Find the buttons** | Several passes at different sensitivities, looking for shapes both *lighter* and *darker* than the body — some remotes are one, some the other — then a vote across the passes. A shape containing three or more others is a recessed panel rather than a button, so it is dropped and its contents kept. | **0.48 s** |
| 6 | **Name the colours** | Each button gets a coarse colour name: red, grey, orange. Never an exact colour value — studio lighting and phone lighting disagree about exact colours but agree about "red". | 0.06 s |
| 7 | **Which way up?** | First guess from the button layout alone. | 0.003 s |
| 8 | **Read the text** | The printing is read from **the whole remote at once**, not button by button — one pass, or two if the way-up is still uncertain, in which case the words themselves settle it. Source watermarks are stripped right here, before anything downstream can mistake a stamp for a button legend. | **7.16 s** |
| 9 | **Use the text** | Four jobs at once: delete "buttons" that were really printed words; attach nearby words to buttons as labels; look for a brand name; look for a model code. The model number is **not** a separate reading pass — it is a pattern search over text already read. | 0.009 s |
| 10 | **Write the fingerprint** | Button positions, sizes, shapes and colours, plus the text, the proportions, the brand and the code. | instant |

## Part B — finding it in the catalogue

The catalogue holds about 12,000 fingerprints. Comparing the query against all
of them properly would be far too slow, so it happens in two stages: a cheap
shortlist, then an expensive check on the shortlist only.

| # | Stage | In plain words | Time |
|---|---|---|---|
| 11 | **Model code shortcut** | If a model code was read off the remote, look up records carrying that code. This narrows the field; it does **not** skip the checks below, because a misread code must still prove itself. | instant |
| 12 | **Describe as "tokens"** | The fingerprint is broken into small features — roughly *what kind of button, where, next to what*. | 0.02 s |
| 13 | **Shortlist** | Look those features up in an index, the way a search engine looks up words. Rare features count for much more than common ones — every remote has a power button, so it says almost nothing. This narrows 12,000 records to **100** candidates. | 0.03 s |
| 14–16 | **Check, score, decide** | For each of the 100, try to lay the two button patterns on top of one another and count how many actually line up. Then combine that with the shortlist score, whether the brands agree, whether the proportions agree, and a bonus if the model codes match. Finally sort, and decide how confident to be. | **2.19 s** |

The answer is reported in one of four confidence bands — **high / medium / low /
none** — based on the top score *and* how far ahead it is of the runner-up. Two
answers three thousandths apart is not a confident answer, it is a tie.

---

## Where the 10 seconds actually go

```
reading the printed text   ############################################   7.2 s   72%
checking the 100 candidates ##############                                2.2 s   22%
finding the buttons        ###                                            0.5 s    5%
everything else            .                                              0.1 s    1%
```

Two stages are 94% of the work. Everything people usually imagine is expensive
— opening the file, finding the remote in the photo, cutting it out, deciding
which way up it is, describing it — is together about **one hundredth** of the
time.

That has a practical consequence: making the *search* cleverer would barely
move the clock. Only two things can make this meaningfully faster — reading
less text, or checking fewer candidates.

## What changes the timing

- **Several remotes in one photo multiplies stages 4–10.** Each one is cut out,
  measured and read separately. The photograph of a remote lying on its
  instruction manual produced four "remotes" and took **2.6 times longer** than
  a clean single-remote photo.
- **A busier remote costs more.** More buttons means more shapes to trace and
  more legends to read.
- **The catalogue's size barely matters to the shortlist** (stage 13 is
  hundredths of a second) but sets the cost of stage 14 — 100 candidates is
  100 comparisons, whatever the catalogue holds.

## The catalogue side is the same pipeline, run differently

Building the catalogue uses stages 1–10 unchanged, with three differences:

- **No time budget.** The text is read twice at higher magnification, and the
  button-finding runs more passes. Accuracy is worth any amount of offline time.
- **Extra refusals.** A crop with no buttons and no text, a crop that is nearly
  empty beside a busy neighbour, or a crop whose buttons are denser than its
  pixels could possibly resolve, is rejected and *counted*. A record that
  vanishes without a reason is indistinguishable from a genuine gap in the
  catalogue.
- **Then two consumers are loaded** from the same run: the search index, and
  the database table the website reads. If those two ever come from different
  runs, searches return records that resolve to nothing — which looks like a
  database fault and is not.

## Where the numbers come from

Measured on the deployment box (2 processor cores) against `2749.jpg`, an
ordinary single-remote catalogue photograph, with the service warm. Internal
work totalled 7.1 seconds; the live endpoint reported 7.9 seconds for the same
photo, the difference being network and request handling. Every figure above is
that measurement multiplied by 1.41 so the total reads as an even 10 seconds.

The four-remote photograph (`2750.jpg`, a remote beside its instruction
leaflet) took 18.3 seconds in stages 1–10 alone.

**This is slower than intended.** The design budget for a search is about one
second. It is not being met on this hardware, and the reason is now known
precisely: reading the text.
