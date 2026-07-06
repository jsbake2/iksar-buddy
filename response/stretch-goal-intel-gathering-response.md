  Gap 1 — the reaction model (biggest, and where doc ≠ reality)

  craft.yaml/sensors.py currently assume 3 reaction buttons at hotbar y≈980, grabbed fresh, and a single fixed region where "the active reaction" appears. The screenshots show something different:

  - The counter arts are 6 hotbar icons (your red boxes), not 3. The doc's "3 counters × 2 modes (dur 1–3 / prog 4–6)" may still be the right key model, but the match-reference set is 6 icons, not 3.
  - The active event renders in the right component panel as a vertical stack ("Heat Loss" sat below three ?/"Unknown" rows). That stack grows/scrolls — so the event is not at one fixed y. A single small region box will miss it. This is the #1 detection risk.
  - The elegant design this implies: event icon == counter-art icon, so matching the popped event against the hotbar art icons tells you which key to press. Need to confirm that's true.


  Gap 1 response:
    There are 6 counters like you said, the first three counters increase durability when its low and the second sed of three increase progress.
    The 'counter' we care about is always in the EXACT same spot, not sure what you are talking about with drift on y... its not true. The counter will be an EXACT match to one of the three pairs of craft buttons.
    We will be detecting durability levels and when durability is high, we use the progress versions of the counters and when durability is low, we use the durability version of the counters. 

  Gap 2 — Create vs Begin vs Retry are three buttons; code knows only two

  sensors.begin_or_retry + craft.yaml model begin + retry only. You added Create (pink). In img1 both Create and Begin show pre-craft simultaneously. The worker state machine needs the exact click sequence and when each appears (e.g. Create = commit recipe+qty, Begin =
  start reaction phase, Retry = repeat). That's your SME call.

  Gap 2 response:
  
  The create should not be necessary but I figured we could throw it in there as a one-off in case begin is not there or something... 
  When you search for an item and click it, the begin button will always be there the first craft and the 'retry' or 'redo' whatever... will be there subsequent runs.

  Gap 3 — still uncaptured (not in these three shots)

  - Durability/Progress mode pixel (#4) — I can see the dual bars (green progress + red durability) in the right panel, but need the exact pixel+colors.
  - Power/mana gate pixel (#5).
  - Search: clear-X (#9) and first-result row (#11) — you gave the search box but not these two.
  - Journal OCR region (#12) — separate quest-journal window.

  Gap 3 response:

  Pixel provided in a screenshot
  Mana value lower than 50 percent should cause a pause unless we are doing a writ, if we are doing a writ we just barrel forward until the writ is complete.
    ** For mana value, look at the healer code and see if you can use that region for healer mana level to figure out our mana value.
  I will give you a better idea of the search box this time around.
  I will define the OCR region in a screenshot attached to this response.

  What I need from you (SME)

  1. Reaction matching: is the popped event icon the same artwork as its counter art on the hotbar? And is it 3 distinct counters (×2 modes = 6 keys) or 6 independent arts?
      - answered above in Gap 1 response.
  2. Event position: does the active event always render at the same spot (e.g. always the bottom-most row / a fixed "current event" slot), or does it float as the stack grows? If fixed, point me at the slot; if floating, I watch the whole column.
      - answered above in Gap1 response.
  3. Active cue (calib #3): what visually marks the one to press right now — the green-boxed row highlight/glow, a timer bar, something else?
      - when there is no counter present, we will push 1,pause .3 seconds, 2, pause .3 seconds, 3 -- do that again and again but break IMMEDIATELY when we see a counter. If our durability is high then we spam 4,5,6 with the pauses instead. 
  4. Button flow: the Create → Begin → Retry sequence and when each is clickable.
      - when the window first opens it looks like both create and retry are available but lets just rely on the Begin button for now and save the Create into a variable as well in case we need to change direction later.

  ***You did a very poor job I think on the images. I am going to find a smarter way to mark them up. Each of these images is a modified version of the original. This is to allow me to put very bold markings that cover up the portion of the screen I want you to see and you can compare new image to old to see what I am covering and hopefully you can get better results.***


Things to capture (for me to track and some nice descriptions for you to read):
1. Begin button (the start button after you locate the correct recipe)
2. Text input field (this is where you will search for recipes, we need to work on precision searching. I will also be limiting the search to our level range if this becomes too challenging)
3. Counter icon area (the icon that we counter with one of our crafting hotkeys)
4. Craft skill buttons (1,2,3 specifically)
5. Retry craft button (the retry button for crafting the same item again)
6. Create button (may actually be needed on a failed craft, I know something odd happens when we fail and I will definitely clue you in when I see this condition)
7. Safe area for mouse (this is where you put the mouse and click when each craft starts, this is to make sure we dont use our standard hotbar with the crafting skills and it keeps us out of chat)
8. Recipe area that filters when we search a name and press ENTER (this list should reduce down to one recipe with a good list)
9. Quest journal area to look for writ (this is just an FYI until we actually start doing writs)

(Screenshots below were moved out of the repo to `~/ib-data/archive/craft-vm1-screenshots/` on the workstation.)

EQ2_000002.png
  - pink-input_white-recipe-list.png
EQ2_000004.png
  - mouse-safe-pink.png
EQ2_000005.png
  - begin_red-create_green.png
  - 1-red_2-orange_3-green_react-blue.png
EQ2_000006.png
  - quest-area-blue.png
EQ2_000008.png
  - durability-low-yellow.png


Additional Notes:

1. You will have to take the 3 craft buttons (1, 2, 3) and capture them into your memory to use as counters every time you start a new craft. 
2. We will need to do the log capture
3. We need to do the OCR capture for quests but I first have to hit level 20 on a crafter. To get there we will iron out the standard bulk crafting and level up - recipe log crafting. 
4. Level up recipe log crafting is referring to when I level up, read a new recipe book, and you capture all new recipes - regex them to get rid of stuff we dont care about - and you add them to a crafting list for us to work off. A savable list that I can use again in the future. 
5. If you read the dinosaur code or found references to it then you are aware of the regex challenges as well as the challenge around how much text can go into an input field. This is critical for long recipe names so we appropriately chop down words to make the search fit.
6. If anything remains unclear after this novel length response, please ask. I don't want you to go forward with bad assumptions when this is my SME area... Please ask before writing a bunch of code.
7. For our test we will craft leather bags since I need those for all my crafters for now. I will get you the exact recipe name when its time and we can do those to test with. 
8. Please snip the things you think you should, label them and put them into the claude_findings folder so I can see that you really do understand what we are doing here. 