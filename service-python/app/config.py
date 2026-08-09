"""Central configuration. Every magic number in the pipeline lives here.

Tuning workflow: change a value, re-run scripts/extract_one.py, look at the
overlay. Never scatter thresholds through the pipeline modules.
"""
import os
import re

from dataclasses import dataclass, field


@dataclass
class BodyConfig:
    """Detection of the remote body within a catalog or query image."""
    # a body must occupy at least this fraction of the image
    min_area_frac: float = 0.02
    # remotes are elongated: length/width must fall in this range
    min_elongation: float = 1.6
    max_elongation: float = 9.0
    # morphological closing kernel, as a fraction of the image's short side
    close_frac: float = 0.02
    # border sampling width used to estimate background colour
    border_px: int = 8
    # a body candidate touching the image edge on 3+ sides is the frame itself
    reject_edge_touching: bool = True
    # blobs less elongated than this are probed for a multi-remote split
    split_below_elongation: float = 2.6
    # a blob covering more than this fraction AND touching 3+ edges is the
    # image frame, not a remote
    frame_area_frac: float = 0.80
    # ...but only when it is also rectangle-solid. A segmentation that
    # swallowed the background fills its bounding rect almost completely; a
    # real remote has rounded corners and fills about 0.80-0.88. Without this
    # a tightly-cropped catalog image (remote at 82% of frame) is thrown away
    # as "the frame".
    frame_min_fill: float = 0.95
    # A frame-sized blob is only spared as a tight crop when it is elongated
    # enough to be one remote. Relaxing the frame rule on fill alone let a
    # near-square blob holding *two* remotes and their packaging through as a
    # single body (RM-L859-1: elongation 1.80, 38 buttons, two remotes fused
    # into one fingerprint). A genuine tight crop is far more elongated than
    # a montage, so this is the discriminator that separates them.
    frame_min_elongation: float = 2.5
    # a single body larger than this is the background, not a remote
    single_body_max_area_frac: float = 0.90

    # --- tight-crop fallback ----------------------------------------------
    # Scraped catalog images are often cropped hard to the product, leaving no
    # background at all. Border sampling then estimates the *remote* as the
    # background and the mask inverts: the ROLSEN RSF-3106RT segmented to just
    # its white wordmark and yielded no body whatsoever. When neither strategy
    # finds a body, treat the whole frame as the body provided the frame is
    # itself remote-shaped. A slightly loose crop beats no extraction at all.
    full_frame_fallback: bool = True
    # inset the frame slightly; catalog crops usually keep a hairline border
    full_frame_inset: float = 0.01

    # The fallback above used to fire only when segmentation found *nothing*,
    # which missed the commonest form of the same failure: a body the same
    # colour as the backdrop -- light grey on white, white on white -- where
    # the border estimate lands on the product, the mask inverts, and what
    # survives as foreground is only the high-contrast interior. A fragment of
    # that is then returned as a confident body, so `not bodies` is never true.
    #
    # Two records, both extracted from a ~6% fragment, with the frame treated
    # as the body instead:
    #
    #   8081000_0                   3 buttons q=0.425  ->  16 buttons q=0.956
    #   Electrolux-YAC1FBI-copy_0   1 button  q=0.443  ->  19 buttons
    #
    # A body under this fraction of a frame that is itself remote-shaped is not
    # a remote. 1566 of 12669 records (12.4%) carry that signature.
    #
    # Only when exactly ONE body was found: several small bodies is what two
    # remotes side by side legitimately look like, and 21.7% of the catalogue
    # is that. A cheaper test -- border colour close to interior colour -- was
    # measured first and rejected: it fires on 28% of bad extractions and 12%
    # of good ones, which is not a discriminator.
    min_plausible_area_frac: float = 0.15

    # And a second reading of the same question, for the case the area floor
    # misses: do the bodies, taken together, *span* the frame?
    #
    # 8081000 is a photograph of one remote. Segmentation returned two bodies
    # -- two vertical slabs side by side, both covering only y 241-580 of a
    # 785-tall frame -- so the area total is 0.31 and clears the floor, while
    # their union bounding box covers 0.32 of the frame. Two remotes in one
    # photograph fill it; fragments of one remote sit in a band.
    #
    # Measured over multi-body images, union bbox as a fraction of the frame:
    #
    #   total area < 0.15    median 0.07   100% below 0.60
    #   total area 0.15-0.45 median 0.44    76% below 0.60
    #   total area >= 0.45   median 0.90     2% below 0.60
    #
    # So this costs about 2% of genuine pairs and recovers three quarters of
    # the middle band. Applied only when there is more than one body: a single
    # body is already judged on area, and a lone real remote in a loose frame
    # would otherwise be replaced by the frame for no reason.
    min_bodies_span_frac: float = 0.60

    # And a count. The two tests above ask whether the bodies are big enough
    # and spread enough; neither catches a mask that fragmented along the
    # *rows* of a keypad, because the strips are individually plausible and
    # together they cover the remote.
    #
    # NetUP_Android_IP_STB -- a black remote on a dark ground -- came back as
    # ten bodies, each one horizontal row of keys, aspect 5.8 to 6.8, 2.5% of
    # frame each. Total area 0.263 clears the 0.15 floor and union span 0.746
    # clears the 0.60 floor, so both guards passed a segmentation that had
    # found ten remotes in a photograph of one. Treated as a full frame it
    # yields 60 buttons.
    #
    # A catalogue photograph holds one remote, sometimes two colour variants
    # side by side, occasionally three. It does not hold ten. In the rebuilt
    # catalogue 90.8% of images give one body and 5.4% give two; more than
    # three covers 262 images (2.3%), whose mean quality is 0.711 against a
    # catalogue median of 0.86.
    # One remote per photograph is the rule; two is allowed only on strong
    # evidence, and more is always a fragmented mask. 90.8% of the rebuilt
    # catalogue gives one body and 5.4% gives two, so this costs almost nothing
    # and removes the whole class where strips of one keypad pass as several
    # remotes.
    max_plausible_bodies: int = 2
    # What "very sure" means for a pair: each of the two must be a substantial
    # object in its own right, and together they must span the frame the way
    # two products photographed side by side do. A pair that fails either test
    # is two pieces of one remote.
    pair_min_each_area_frac: float = 0.15


@dataclass
class NormalizeConfig:
    """Rectification to canonical upright form."""
    out_width: int = 400
    # pad the detected body slightly; button rims often sit on the silhouette
    pad_frac: float = 0.015
    max_aspect: float = 12.0

    # Source images whose LONG side is under this are refused by the catalog
    # build. Rectification upscales every body to out_width regardless of what
    # it started as, so a thumbnail is not merely a poor image: it is enlarged
    # 20x and detection then traces interpolation artefacts as keycaps. A
    # 16x50 imagecache thumbnail extracted 29 confident "buttons" this way,
    # indexed, and self-matched at a constant 0.925 -- 72 of 91 records in the
    # session-6 calibration were such thumbnails, which is why its bands looked
    # perfect and meant nothing.
    #
    # Long side, not both sides, and not area: a remote is elongated, so the
    # real catalogue standard is ~303x1090. Requiring 600 on both sides rejects
    # 52 of the 62 usable images in the dev sample; requiring it on the long
    # side rejects all 75 thumbnails and none of them.
    min_source_long_side: int = 600

    # A crop is only turned upside down when the geometry is *sure*. The old
    # bar was `not ambiguous`, i.e. confidence >= 0.25, which is barely better
    # than a coin toss: the Supra STV-LC1504 was flipped at 0.596, its single
    # OCR pass then read an inverted crop and returned zero text, and the model
    # code printed plainly at the bottom was lost.
    #
    # Flipping the wrong way is not symmetric with leaving it alone. Almost
    # every photograph, from a catalogue shoot or from a phone, is taken
    # roughly upright; the prior strongly favours "as given", and a wrong flip
    # costs the text as well as the geometry. Where the evidence is weak the
    # honest answer is to leave the crop alone and let the *matcher* try both
    # ways -- which it already does, on both sides.
    #
    # Measured over the 13584-record catalogue: 493 records (3.6%) are flipped,
    # 288 of them at confidence 1.00 from the text signal. Only 130 (0.96%)
    # flip on evidence below 0.75.
    flip_min_confidence: float = 0.75

    # And a ceiling, in pixels rather than bytes. A 12924x3144 upload -- 41 MP,
    # 9 MB, well inside max_upload_bytes -- OOM-killed the service on rcud
    # inside its 1 GB limit and returned a 503 to the caller. Bytes are not the
    # constraint: JPEG of a flat product shot compresses hard, so the file size
    # says almost nothing about the decoded array, and every stage after decode
    # holds a multiple of it.
    #
    # Downscaled, not refused. A 48 MP phone photograph is legitimate input and
    # nothing is lost by shrinking it: rectification takes the body to
    # out_width (400) regardless, and OCR runs on the crop at its own upscale.
    # 12 MP is above any catalogue image and above what a phone camera produces
    # at default settings.
    max_source_pixels: int = 12_000_000


@dataclass
class ButtonConfig:
    """Button detection within the rectified body."""
    # button area as a fraction of body area
    min_area_frac: float = 0.00035
    max_area_frac: float = 0.060
    # contour area / bounding-box area. Text strokes and rims score low.
    min_fill: float = 0.45
    # reject extreme slivers
    max_aspect: float = 6.0
    # Adaptive-threshold block sizes, as fractions of the crop WIDTH.
    # Absolute pixel sizes do not survive a change of crop resolution: a
    # block that works at 400px finds nothing at 900px.
    #
    # The largest of these has to exceed the largest BUTTON, not merely be
    # large: adaptive thresholding compares a pixel to the mean of its block,
    # so a block smaller than the feature sees the feature's interior as its
    # own background and only fragments of the rim survive the area floor.
    # Denon RC-982 (record 2789) is 152x616 at source, rectified to 400x1763,
    # and its keys are ~14% of crop width against a largest block of 11.5%:
    # every pass returned **zero** buttons on a remote with forty obvious ones.
    #
    #   block frac   0.045  0.075  0.115  0.150  0.200  0.250  0.300
    #   buttons          0      0      0      5     23     40     40
    #
    # Adding 0.25 across 23 records: 21 unchanged or better, one worse by 2,
    # total detections 494 -> 573. The ones it rescues are the records this
    # project already knew were bad -- Huayu_RM-530F 2 -> 7, Prestigio_KF-7777A
    # 24 -> 34, ClickPdu_RM-D1110 7 -> 12.
    #
    # Costs two more passes (one per polarity). detect_buttons is ~70 ms of a
    # 2900 ms extraction, so this is not where the time goes.
    block_fracs: tuple = (0.045, 0.075, 0.115, 0.25)
    adaptive_c: int = 5
    # two detections overlapping by more than this are the same button
    dedupe_iou: float = 0.35
    # with ensemble=True a button must be found by at least this many passes
    min_votes: int = 2
    # a pass yielding fewer than this is treated as failed and excluded from
    # the vote entirely, rather than being allowed to veto the others.
    # NOTE this is an absolute count on purpose, but a remote with genuinely
    # few buttons can have every pass fall below it -- see the fallback in
    # detect_buttons, which keeps the best pass rather than returning nothing.
    min_pass_yield: int = 4

    # --- ring rescue -------------------------------------------------------
    # A low-contrast button often thresholds as an outline rather than a disc,
    # and a *broken* outline is traced as a stroke, so contour area measures
    # the stroke and min_fill throws the button away. (This returned zero
    # buttons on the HONEYWELL HE5500: grey buttons on a grey bezel.) Where a
    # contour looks like a ring, the convex hull is measured instead.
    # The guards keep letter glyphs out: a rescued ring must be round, large,
    # and hollow.
    ring_rescue: bool = True
    ring_max_aspect: float = 1.4      # round: rules out most lettering
    ring_min_hull_fill: float = 0.60  # hull fills its bounding box
    ring_max_solidity: float = 0.60   # hollow, not a solid blob
    # a rescued ring must be this many times the minimum button area. Text
    # strokes that survive the other guards are small; real ring buttons are
    # the big circular ones.
    ring_min_area_mult: float = 8.0


@dataclass
class ColorConfig:
    """Coarse colour bucketing of buttons."""
    # sample the inner fraction of the button to avoid rim highlights
    inner_frac: float = 0.6
    value_black_max: int = 65
    sat_grey_max: int = 85   # tinted plastics read as low-sat colour
    value_white_min: int = 185
    hue_ranges: dict = field(default_factory=lambda: {
        "red":    [(0, 8), (172, 180)],
        "orange": [(9, 22)],
        "yellow": [(23, 33)],
        "green":  [(34, 85)],
        "blue":   [(86, 130)],
        "purple": [(131, 158)],
    })


@dataclass
class OcrConfig:
    """Text detection and recognition.

    The engine is swappable (app/pipeline/ocr.py). PaddleOCR is the engine the
    plan specifies, but RapidOCR runs the same PP-OCR models under onnxruntime
    at a fraction of the footprint, so it is first here. Order is preference,
    not requirement: the first installed engine wins.
    """
    #
    # `openvino` is the same PP-OCR models under OpenVINO and is roughly twice
    # as fast (see RapidOcrOpenvinoEngine), but it is deliberately NOT first:
    # it reads more text than the onnxruntime build, so it changes
    # fingerprints, and a catalogue built under one engine and queried under
    # another is the asymmetry this project keeps finding. Put it first only
    # together with a rebuild -- RCU_OCR_ENGINE=openvino does that for one
    # process without editing this tuple.
    engine_order: tuple = ("rapidocr", "openvino", "paddle", "easyocr",
                           "doctr", "tesseract")
    # OCR the crop upscaled to at least this width. The 400px canonical crop
    # renders VOL as about 6px tall, which no engine reads.
    min_width: int = 1100
    # discard recognitions below this confidence outright
    min_conf: float = 0.35
    # For the offline catalog build, run several engines and keep a region
    # only when this many agree, or when one engine is very confident.
    consensus_min_engines: int = 2
    solo_conf: float = 0.90
    # two boxes from different engines are the same region above this IoU
    match_iou: float = 0.30
    # per-image ceiling; a crop yielding more than this is noise, not labels
    max_regions: int = 120

    # --- how much image the detector actually sees ------------------------
    # PP-OCR resizes before detection, and its own default is `min`/736, which
    # only ever *upscales*: our 800x2346 crop is already above the floor, so it
    # is detected at full size and det is ~75% of OCR time. `max` caps the long
    # side instead, which is the only knob that reduces the area.
    #
    # Measured over the 18 dev photographs, same crops, brand and model code
    # compared against the current setting:
    #
    #   min/736 (default)   1831 ms   420 regions   --
    #   max/1600            1087 ms   403 regions   17/18 agree
    #   max/1280             849 ms   401 regions   18/18 agree
    #   max/960              668 ms   381 regions   17/18 agree
    #
    # 1280 is the knee. Note 1600 loses `RM-PJ20R` -> `RM-PJ20` where 1280 does
    # not, so the effect is not monotone in the limit and 18 images is a small
    # sample -- that trailing R is a different remote as far as the index
    # cares, which is the same truncation the model-code regex was fixed for.
    #
    # NOT adopted by default: changing what the pipeline reads changes
    # fingerprints, and a catalogue built under one setting and queried under
    # another is the query/catalog asymmetry this project keeps finding.
    # Change both paths together, with a rebuild behind it.
    det_limit_type: str = "min"
    det_limit_side_len: int = 736
    # OCR the crop in horizontal bands no taller than this. An upscaled
    # remote is ~1100x4500, and detection over the whole thing at once needs
    # more RAM than a small VM has. Bands also keep detection resolution high
    # on long remotes.
    tile_max_height: int = 1400
    # band overlap, as a fraction of band height. Must exceed the tallest
    # expected text, or a label on a seam is cut in half.
    tile_overlap: float = 0.10
    # two regions from neighbouring bands overlapping this much are one
    dup_iou: float = 0.55
    # onnxruntime threads per model. The default (-1, one per core) blows the
    # memory budget on a small VM for no speed gain on images this size.
    threads: int = 1

    # Text-line angle classifier (PP-OCR's det -> cls -> rec middle stage).
    # MUST STAY FALSE. Both RapidOCR and Paddle enable it by default, and it
    # rotates each detected text line upright before recognition -- so a crop
    # and the same crop rotated 180 degrees recognise *identically*, and the
    # text orientation signal is not weak, it is exactly zero. Measured on the
    # Sony RM-PJ20, one band:
    #     use_cls=True   upright 6 vocab hits, flipped 6   (no signal)
    #     use_cls=False  upright 6 vocab hits, flipped 0   (perfect signal)
    # This, not weak taper, is why session 2 left 11 of 21 crops unresolved.
    # The cost of False is that a genuinely upside-down legend on an otherwise
    # upright remote goes unread. That is a bonus-term loss (labels never gate
    # a match), whereas a wrong orientation silently corrupts a whole record.
    angle_cls: bool = False

    # --- query-path economy (fast_ocr=True) --------------------------------
    # The offline build OCRs both ways up at full upscale and has no time
    # budget. A query has about a second, and measured on this box the offline
    # settings cost ~18 s: ~7.6 s per OCR pass, twice, plus 0.6 s of geometry.
    #
    # Upscale for a query. At 1100 the Sony crop takes 7.0 s and at 800 it
    # takes 3.3 s for the *same* 38 regions -- the wordmark and model code are
    # among the largest text on a remote and do not need the full upscale.
    # Small keycap legends do, which is why the catalog side keeps min_width.
    query_min_width: int = 800
    # Whether a query OCRs both orientations to get the text orientation
    # signal. It should not: the matcher already retrieves and verifies an
    # orientation-ambiguous query both ways up (matching/verify.py
    # query_is_ambiguous), so the second pass spends ~7 s deciding something
    # the matcher decides again regardless. Offline, the signal is worth it.
    query_both_orientations: bool = False
    # ...but geometry is allowed to be *sure*. Below this confidence the query
    # OCRs both ways after all, because the reasoning above only covers
    # retrieval: the matcher deciding orientation again cannot recover text
    # that was never read. A query commits to geometry's guess before its
    # single OCR pass, so a wrong guess costs the brand and the model code --
    # the largest single term in the fusion -- and nothing downstream can get
    # them back.
    #
    # Supra STV-LC1504, photographed by hand: geometry said flipped at
    # confidence 0.596, the one OCR pass read an upside-down crop and returned
    # **zero** text regions, and the model code printed plainly at the bottom
    # was lost. The same image on the build path reads STV-LC1504 at
    # confidence 1.00.
    #
    # Kept equal to normalize.flip_min_confidence, so there is one rule rather
    # than two: below the confidence at which geometry is allowed to turn a
    # crop over, its verdict is not acted on *and* not relied upon -- the query
    # re-reads both ways and the text decides. Leaving these unequal opens a
    # band where a query is neither flipped nor re-read, which is the worst of
    # both. About 4% of records fall below 0.75.
    query_text_orientation_below_conf: float = 0.75

    # --- source watermark suppression --------------------------------------
    # Catalogue images scraped from a site usually carry that site's watermark
    # stamped across the middle. It must be dropped *at the OCR boundary*, so
    # that no later stage ever sees it: it would otherwise become index tokens,
    # be assigned as a button label, be read as a brand, and -- worst -- feed
    # the text orientation signal, which it would always resolve upright
    # because the stamp is upright however the remote is lying.
    #
    # Matched fuzzily, because a semi-transparent stamp over a textured keypad
    # OCRs in pieces and with errors. Measured over 30 pultov.net images, one
    # stamp read as all of:
    #     PULTOV.NET  PULTOVNET  PLTOVNET  PULTOV.NE  ULTOV.N  OV.NET  V.NET
    #     VNET  OVNET  ANET  Y.NET  PULTCV  PULT5V.RET  PULIC  OV.NAI  PUO  ET
    # so an exact or substring test catches almost none of it.
    #
    # Master switch. Source images are not always watermarked -- the same
    # catalogue may be re-supplied clean -- and this must be one flag to flip,
    # not a code edit. Also settable per run:
    #     RCU_WATERMARK_FILTER=0                 env, applies to the service too
    #     extract_one.py --no-watermark-filter   one build
    # Turning it off costs nothing on clean images: the check only ever fires
    # on large centred text that fuzzy-matches a configured term.
    watermark_filter: bool = True
    # The stamps to remove, in A-Z0-9 form. Override for another source with
    # RCU_WATERMARK_TERMS=FOOBAR,BAZ (empty value also disables the check).
    watermark_terms: tuple = ("PULTOVNET",)
    # rapidfuzz partial_ratio against the terms above, on the A-Z0-9 form.
    watermark_min_similarity: int = 60
    # Fraction-of-CROP-height band the stamp lies in, or None for anywhere.
    #
    # None, and the reason is a calibration trap worth remembering. The stamp
    # is centred on the *source image*, so on full images it reads at y
    # 0.47-0.51 and a tight band looks perfect. But OCR runs on the rectified
    # crop, and the remote occupies a different part of every frame: measured
    # on the crops the same stamps land anywhere from y 0.29 to 0.50. A band
    # fitted to the full image silently passed the stamp through on
    # mysteri_RB-32K101U, which stored "PULTOV.NET" as a caption.
    watermark_band: tuple | None = None
    # Height relative to the crop's median text height. THIS IS THE TEST THAT
    # MATTERS and it is not optional: a stamp is set much larger than a keycap
    # legend, and without it the similarity test alone deletes real buttons.
    # Measured on the rectified crops, above the similarity threshold:
    #     watermark fragments  1.42x - 4.42x  (none below)
    #     real legends         EXIT 0.9-1.2x on four remotes, NETFLIX 1.1x
    # EXIT scores 67 against PULTOVNET and NETFLIX 60, so similarity alone
    # deletes both. 1.3 sits in the gap, and with the band gone this test is
    # the only thing standing between the filter and real buttons.
    watermark_min_height_ratio: float = 1.3
    # Single characters match almost anything at partial_ratio.
    watermark_min_len: int = 2


@dataclass
class LabelConfig:
    """Assignment of text regions to buttons, and text-vs-button arbitration."""
    # a text box further than this from any button centre is a group caption
    # (SOURCE, SETTINGS) rather than a button label
    max_assign_dist: float = 0.09
    # fraction of a button's area that must be covered by text boxes before
    # the detection is suspected of being printed text rather than a button
    text_overlap_frac: float = 0.60
    # ...unless the detection is this much bigger than the text covering it,
    # in which case the text is printed ON a real button (digit on a keycap)
    keep_area_ratio: float = 2.2
    # a text box wider than this fraction of the crop is a caption strip or a
    # brand wordmark, never a button
    caption_width_frac: float = 0.55


@dataclass
class BrandConfig:
    """Brand identification and model-code extraction."""
    # rapidfuzz score (0..100) required to accept a brand match
    fuzzy_threshold: int = 85
    # Whether the unverified-wordmark fallback may name a brand at all.
    #
    # OFF, on its measured record: across 57 catalog records (21 sample + 36
    # scraped) it has fired exactly once and was wrong -- "Ind" on ACER
    # AT2603, invented out of OCR garbage on a 166x599 image at conf 0.71,
    # having cleared every structural guard (3+ letters, no digits, isolated,
    # not a known legend, 2.2x median height). Session 4 separately found no
    # record on the 21-image sample used this path at all.
    #
    # So it has produced no correct brand and one false one, and a false brand
    # is worse than none because `brand_agreement` believes it. Brands that
    # are actually printed are read by the verified list path, which scores
    # 0.99 where this scores 0.36.
    #
    # Kept behind a flag rather than deleted: on a catalog full of marques
    # missing from the brand list it may yet earn its place. Turn it on only
    # with a measurement that says so.
    unverified_wordmark: bool = False
    # unmatched text this many times the median text height is a candidate
    # brand wordmark, stored unverified
    big_text_ratio: float = 2.2
    # model codes are rarely shorter than this once punctuation is stripped
    model_min_len: int = 5
    # codes usually sit below the keypad, near the bottom of the body
    model_bottom_y: float = 0.75
    # no other text within this radius means an isolated, code-like region
    isolation_radius: float = 0.12
    # a text region with a button this close is probably a button legend
    button_radius: float = 0.05
    # ...scored down rather than excluded, so a code printed just under the
    # keypad can still win on its other evidence
    button_penalty: float = 0.6
    # a hyphen or space inside the token: RM-530F, RC-371M, MR-18B
    separator_bonus: float = 0.75
    # An unverified wordmark needs this many letters. Without it the largest
    # text region on a crop can be "+" or "S", and that becomes the brand.
    min_wordmark_alpha: int = 3
    # A wordmark stands on its own; a button legend has its neighbours around
    # it. Nothing else may sit within this radius of an unverified wordmark.
    # Separate from `isolation_radius` (model codes) because a wordmark sits in
    # clear space at an end of the body, while a code often sits just under
    # the keypad and needs the looser test.
    wordmark_isolation_radius: float = 0.10
    # Tokens shorter than this must match a known brand EXACTLY; only longer
    # ones may match fuzzily. At the fuzzy threshold a single edit is a small
    # penalty on a short word, so button legends collide with short brand
    # names: SPACE scored 88.9 against "Pace" and IRIS 88.9 against "Irbis",
    # both above the threshold. Every brand this pipeline reads correctly
    # (SONY, ONKYO, HUAYU, WINK, GINZZU, HONEYWELL) matches exactly anyway,
    # so this costs no recall and removes a whole class of false brands.
    #
    # NOTE do NOT filter regions by role=="label" here. It looks right and is
    # badly wrong: label assignment uses a 0.09 radius, so on a button-dense
    # remote nearly every region becomes a "label" -- including the SONY
    # wordmark and the RM-PJ20R code -- while genuine body text stays a
    # "caption". Filtering on it dropped the correct model code and kept OCR
    # garbage.
    fuzzy_min_len: int = 6


@dataclass
class QualityConfig:
    """Plausibility bounds used to score extraction confidence."""
    min_plausible_buttons: int = 6
    max_plausible_buttons: int = 70
    ideal_button_range: tuple = (14, 50)
    # fraction of buttons carrying a label at which label recall scores full
    # marks. Real recall on black remotes is poor; do not set this high.
    good_label_recall: float = 0.35


@dataclass
class AuditConfig:
    """Thresholds for scripts/audit_catalog.py.

    The audit only ever flags; nothing here deletes or rewrites a record.
    """
    # A body this small is nearly always a sub-region the detector latched
    # onto -- a packaging label, a logo panel, a hand holding the remote --
    # rather than the remote itself. Real remotes in this sample span
    # 0.21..0.96 of the frame, while the two known-bad bodies sit at 0.02 and
    # 0.08, so the gap is wide and this does not need to be precise.
    min_body_area_frac: float = 0.12
    # below this the extraction is probably unusable
    low_quality: float = 0.5
    # orientation confidence below this counts as unresolved. Orientation
    # errors are silent and corrupting, so these are worth surfacing even when
    # every other number on the record looks healthy.
    low_orientation_conf: float = 0.35
    # plausible body aspect; outside this the crop is probably not one remote
    aspect_range: tuple = (1.5, 7.0)


@dataclass
class TokenConfig:
    """Token generation. Runs identically at build time and at query time.

    Two families: grid tokens off the rectified coordinates, and triplet
    invariants that survive affine distortion when body detection is shaky.
    """
    # grid resolution across the width and along the length of the body
    grid_x: int = 12
    grid_y: int = 32
    # A button sitting on a cell boundary lands in different cells in the
    # catalog and the query, and its token is then simply lost. Emit the
    # neighbouring cell as well whenever the button falls within this fraction
    # of a boundary. Same class of problem as an absolute threshold: the cost
    # of a near-miss should be graceful, not total.
    grid_edge_frac: float = 0.25
    # button width bucket edges, as fractions of body width
    size_edges: tuple = (0.12, 0.25)
    # Degraded variants of each grid token, emitted alongside the full one.
    # A colour misread or a size misquantisation would otherwise destroy every
    # token for that button. The variants are far more common across the
    # catalog, so IDF automatically discounts them -- there is no weight to
    # tune here, which is the point.
    emit_color_agnostic: bool = True
    emit_size_agnostic: bool = True
    # label text on its own, with no position. Distinctive legends (NETFLIX,
    # TELETEXT) survive a body-detection error that shifts every coordinate.
    emit_label_text: bool = True

    # --- family B, triplet invariants -------------------------------------
    triplets: bool = True
    # neighbours per button considered when forming triplets
    triplet_k: int = 5
    # distance-ratio quantisation (decimal places) and angle bucket, degrees
    triplet_ratio_places: int = 1
    triplet_angle_bucket: float = 15.0
    # triplets are quadratic in k; skip them on very dense remotes where the
    # grid family already has plenty to work with
    triplet_max_buttons: int = 80


@dataclass
class IndexConfig:
    """Inverted index build and tier-1 retrieval."""
    # candidates handed to geometric verification
    top_n: int = 100
    # Tokens below this IDF are near-universal and cost more to score than
    # they contribute. Purely a speed control -- see plan 4.3.
    min_idf: float = 0.15
    # ...as is this: a token appearing in more than this fraction of the
    # catalog is skipped at query time.
    max_df_frac: float = 0.25
    # ...but never skip a token in a catalog too small for the fraction to
    # mean anything. Below this many docs, df ceilings are noise.
    max_df_min_docs: int = 200
    # Index every record BOTH ways up, not just the ones the extractor called
    # ambiguous.
    #
    # Was True through session 2, for a good reason: RM-PJ20_big_light_0
    # carried orientation_conf 1.00 while its stored crop was upside down, so
    # confidence meant nothing and trusting it would have made the record
    # unfindable from a correctly-oriented query with nothing reporting an
    # error.
    #
    # Session 3 found the cause -- the OCR angle classifier was reading
    # flipped crops as fluently as upright ones, so the text vote was noise
    # (see CFG.ocr.angle_cls). With it off, that same record resolves upright,
    # and unresolved crops across the sample went 11/21 -> 2/21. Confidence
    # now means something, so this is False and only genuinely ambiguous
    # records are doubled.
    #
    # Measured on the 21-record sample: 42 docs -> 23, recall@1 unchanged at
    # 8/8, separation unchanged (+0.078 -> +0.077). Halves the projected index
    # at 50k records, ~102 MB -> ~52 MB.
    index_both_orientations: bool = False
    # Used only when index_both_orientations is False: below this confidence a
    # record is still indexed both ways up.
    # Kept in step with normalize.flip_min_confidence: anything the pipeline
    # was not confident enough to turn over must be retrievable both ways up,
    # or a record we left upright on weak evidence -- and which really is
    # upside down -- can be indexed one way and never found. 40 records sit in
    # the band this widens.
    orientation_trust_conf: float = 0.75
    # Whether a QUERY's own orientation confidence may be trusted the same way
    # a catalog record's is.
    #
    # MUST STAY FALSE. The threshold above was calibrated on catalog-quality
    # orientation, which is resolved from text read at full upscale both ways
    # up. A query has strictly less information -- fast_ocr drops the text
    # signal entirely (CFG.ocr.query_both_orientations) -- so it falls back to
    # geometry and can be confidently wrong. Confidence from a weaker
    # determination is not the same quantity, and reusing one threshold for
    # both was the bug.
    #
    # Measured on the sample, trusting the query at 0.6:
    #     RM-PJ20_big_light  query flipped conf 1.00, record upright conf 1.00
    #                        -> own record never retrieved, conf none, 0.275
    #                           on a different remote. Forced the other way up
    #                           it is rank 1 at 0.737.
    #     Huayu_Motorola_15  0.488 low -> 0.901 high, same flip.
    #
    # Unlike index_both_orientations this costs no memory: it is one extra
    # token retrieval and one extra verify per candidate, at query time only.
    trust_query_orientation: bool = False
    # doc length normalisation exponent: 0 none, 0.5 cosine-like, 1 full.
    # Full normalisation over-rewards a sparse record that happens to share a
    # rare token; none lets a 54-button record win everything.
    norm_exponent: float = 0.5


@dataclass
class VerifyConfig:
    """Tier-2 RANSAC geometric verification."""
    # RANSAC reprojection tolerance, in normalised body coordinates
    ransac_reproj: float = 0.04
    # a correspondence needs at least this many points to fit a transform
    min_correspondences: int = 4
    # candidate buttons are only compatible within this size ratio
    max_size_ratio: float = 2.2
    # ...and this far apart after the coarse pre-alignment
    max_pair_dist: float = 0.25
    # a colour mismatch is a penalty on the correspondence, never a veto:
    # white balance between a studio catalog shot and a phone photo moves
    # colours around, and buttons are matched on position first
    color_mismatch_cost: float = 0.35
    # RANSAC iterations and a FIXED seed. Fixed because the whole tuning loop
    # is "change a value, re-run, compare"; a score that wobbles between runs
    # would hide small real changes in sampling noise.
    ransac_iters: int = 400
    ransac_seed: int = 12345
    # transform sanity. Both sides are already rectified, so the honest
    # transform is near identity. There is no perspective term to bound: the
    # verifier fits an affine, having found that every RANSAC estimator in
    # OpenCV 4.10 SIGILLs at 50+ point pairs (see app/matching/ransac.py).
    max_scale_dev: float = 0.45      # |log scale| allowed either way
    max_shear: float = 0.35
    # Label agreement is a BONUS ONLY and is capped here. Never make a match
    # require labels: OCR recall on black remotes under bad light is poor and
    # a hard requirement takes recall down with it.
    max_label_bonus: float = 0.30
    # ...and the bonus only reaches that cap once this many inlier pairs
    # actually carry text on both sides. Otherwise one lucky agreement between
    # the single label each side happened to read is worth as much as twenty,
    # which is the same "weak signal given full weight" mistake as letting a
    # near-empty threshold pass vote with the others.
    label_full_evidence: int = 3
    # unmatched candidate buttons drag coverage down by this much per button,
    # relative -- a 40-button candidate must not win against a 12-button query
    # just because all 12 matched
    asymmetry_weight: float = 1.0


@dataclass
class FuseConfig:
    """Score fusion and confidence banding (plan 4.6).

    Calibrate the bands on a real test set. These are the plan's starting
    numbers and nothing has been measured against them yet.
    """
    w_geometric: float = 0.55
    w_tier1: float = 0.25
    w_brand: float = 0.15
    w_aspect: float = 0.05
    # exact model-code agreement. Deliberately large: this path is near-100%
    # precise, see plan 4.5.
    model_code_bonus: float = 0.40
    fuzzy_model_code_bonus: float = 0.20
    fuzzy_model_max_dist: int = 2
    # brand agreement term: match / conflict / unknown either side.
    # A conflict is weighted, NEVER filtered -- OCR misreads and rebadged
    # remotes are both real.
    brand_match: float = 1.0
    brand_conflict: float = 0.0
    brand_unknown: float = 0.5
    # aspect agreement falls to zero at this relative difference
    aspect_tolerance: float = 0.35
    # Calibrated at last, on 254 uploads through the live query path against
    # the 12311-record catalogue. Sessions 5 and 6 could not do this: 19 clean
    # records contain no wrong answers, and session 6's corpus turned out to be
    # three quarters thumbnails, which is why 41 of its queries returned an
    # identical 0.9250. This run gives 232 distinct scores out of 254, spread
    # from 0.528 to 1.398 -- a distribution rather than a pile.
    #
    #   score  margin   n high   precision   recall of all correct
    #    0.55    0.00      237      100%           98%
    #    0.60    0.10      234      100%           97%
    #    0.65    0.10      231      100%           96%   <- chosen
    #    0.75    0.15      195      100%           81%   <- previous
    #
    # 0.65/0.10 buys 36 more high-confidence answers at unchanged precision.
    # Not 0.55, which the data nominally allows: every query in this run is a
    # catalogue photograph matched against itself, and a phone photograph is
    # harder. The margin left is for that difference, not for the measurement.
    high_score: float = 0.65
    high_margin: float = 0.10
    # `medium` measured 78% precise over 59 queries -- worth showing, not worth
    # asserting. Unchanged for want of evidence to move it.
    medium_score: float = 0.50
    # `low` and `none` remain uncalibrated and this run could not calibrate
    # them: it queries the catalogue with its own photographs, so the right
    # answer always exists. All 13 wrong answers scored above 0.50, and no
    # floor between 0.10 and 0.50 rejected a single one. Calibrating these
    # needs queries for remotes that are genuinely absent -- a real /try upload
    # of a Supra STV-LC1504, not in the catalogue, scored 0.42.
    low_score: float = 0.30
    # A tie in the top two scores is not a weak answer, it is two answers. Over
    # the 254-query calibration every single wrong answer was one: 13 of 13 had
    # a margin at or below 0.003, against a median margin of 0.321 for correct
    # ones. Demoting ties catches all 13 and costs 4 correct answers, leaving
    # 100% precision over the remaining 237.
    #
    # The ties are not all matcher failures, which is why they must not be
    # thrown away. `RS41C0` tied with `RS41C0_1` -- the same product, the same
    # model_id, photographed twice. `olto_Y-72C3` and `19SECAP-org` tied with
    # their own model code listed under a second television brand. Those are
    # right answers scored wrong. Others are genuinely different remotes that
    # look alike: RC4331E against RC4312, two IRC replacements, two portable
    # speaker remotes with six buttons each.
    #
    # Both cases want the same treatment: say there are two and show them.
    # /try already groups candidates by model code, so a tie between two
    # listings of one remote renders as a single group and answers itself.
    tie_margin: float = 0.01


@dataclass
class ServiceConfig:
    """FastAPI service (plan section 5)."""
    host: str = "127.0.0.1"
    port: int = 8600
    # shared secret header checked by middleware; bound to loopback as well
    auth_header: str = "X-Internal-Token"
    # env var the secret is read from; unset means auth is disabled and the
    # service logs a warning at startup
    auth_env: str = "RCU_INTERNAL_TOKEN"
    index_path: str = "/var/lib/rcu/index/tokens.npz"
    # keep this many recent debug overlays for GET /debug/{request_id}
    debug_cache_size: int = 32
    max_upload_bytes: int = 24 * 1024 * 1024
    # query path budget; exceeding it is logged, not enforced
    target_latency_ms: int = 1000

    # --- load shedding ----------------------------------------------------
    # Identification is CPU-bound and holds ~350 MB of intermediate arrays
    # while it segments, on a box with two cores. Running two at once is
    # therefore slower than running them one after another AND doubles peak
    # memory, which is what killed the process the day the catalogue passed
    # 8000 records. So the heavy section is serialised.
    #
    # The point of the limit is not the serialising -- the GIL and the core
    # count would do that anyway -- it is what happens to request 30. Without
    # a bound a burst just queues, every client waits longer and longer, and
    # nothing ever fails; with one, the queue is short and the rest are told
    # to come back. `/try` is reachable by anyone, so this is not hypothetical.
    max_concurrent_queries: int = 1
    # How long a request waits for a slot before it is shed. Roughly one
    # query's work: long enough to absorb an overlap, short enough that the
    # caller is not left holding a connection through a queue.
    queue_wait_s: float = 4.0
    # Backstop at the socket. uvicorn answers 503 above this many concurrent
    # connections, before any of this code runs.
    limit_concurrency: int = 32


@dataclass
class Config:
    body: BodyConfig = field(default_factory=BodyConfig)
    normalize: NormalizeConfig = field(default_factory=NormalizeConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)
    color: ColorConfig = field(default_factory=ColorConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    label: LabelConfig = field(default_factory=LabelConfig)
    brand: BrandConfig = field(default_factory=BrandConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    token: TokenConfig = field(default_factory=TokenConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    fuse: FuseConfig = field(default_factory=FuseConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    # v2 adds labels, text_regions, brand and model_code
    fingerprint_version: int = 2
    # Bumped whenever token generation changes in a way that makes old
    # postings incompatible. The index stores it and refuses to load a
    # mismatch rather than retrieving silent nonsense.
    token_version: int = 1


CFG = Config()


def _apply_env_overrides(cfg: Config) -> None:
    """Environment overrides for the few settings an operator flips per run.

    Deliberately tiny. Thresholds belong in this file where they can be read
    and argued about; this exists only for switches whose right value depends
    on the input rather than on tuning -- above all the watermark filter,
    which must be off for a clean re-supply of the same catalogue and on for a
    scraped one, with no code change either way.
    """
    raw = os.environ.get("RCU_WATERMARK_FILTER")
    if raw is not None:
        cfg.ocr.watermark_filter = raw.strip().lower() not in {
            "0", "false", "no", "off", "",
        }

    # Containers must bind the service interface, not loopback. Only ever set
    # this where the port is unpublished and the network is internal -- the
    # auth header is a shared secret, not a substitute for not being reachable.
    host = os.environ.get("RCU_SERVICE_HOST")
    if host:
        cfg.service.host = host

    port = os.environ.get("RCU_SERVICE_PORT")
    if port and port.isdigit():
        cfg.service.port = int(port)

    # Which OCR engine to prefer. Belongs here rather than in code because
    # switching it changes what the pipeline reads, so the build and the
    # service must be given the same value in the same breath -- a
    # per-deployment fact, not a tuning decision.
    engine = os.environ.get("RCU_OCR_ENGINE")
    if engine:
        name = engine.strip().lower()
        cfg.ocr.engine_order = (name,) + tuple(
            n for n in cfg.ocr.engine_order if n != name)

    terms = os.environ.get("RCU_WATERMARK_TERMS")
    if terms is not None:
        cleaned = tuple(
            re.sub(r"[^A-Z0-9]", "", t.upper())
            for t in terms.split(",") if t.strip()
        )
        cfg.ocr.watermark_terms = tuple(t for t in cleaned if t)
        if not cfg.ocr.watermark_terms:
            cfg.ocr.watermark_filter = False


_apply_env_overrides(CFG)
