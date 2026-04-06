Mastermind (MIPS) - README
Flynn

What it does
------------
Plays the Bagels/Fermi/Pico version of Mastermind in MARS. The program
picks a random 5-digit target with all unique digits (first digit 1-9),
then repeatedly reads a 5-digit guess from the user and prints feedback
until the player wins or concedes.

Feedback per guess:
  Fermi  - one printed per digit that is correct and in the right spot
  Pico   - one printed per digit that is in the target but wrong spot
  Bagels - printed if no digits in the guess appear in the target

How to run
----------
1. Open quiz4 in MARS.
2. Settings -> make sure "Popup dialog for input syscalls" is UNCHECKED,
   otherwise the prompt will appear to print twice (once in the console
   and once in the input popup).
3. Assemble, then Run.
4. When prompted, type a 5-digit number with unique digits and press
   enter. Type 0 to concede.

What works
----------
- Computer generates the target using syscall 42 (random int range),
  with uniqueness enforced by a retry loop and the first digit forced
  into 1-9.
- Single read_int per guess (no digit-by-digit input).
- Fermi/Pico/Bagels scoring for 5-digit guesses.
- Win detection when all 5 digits match in place; prints the guess count.
- Concede on input 0: reveals the target and prints the guess count.
- Input validation:
    * rejects values outside 10000-99999
    * rejects guesses with duplicate digits
  Invalid guesses print an error and do not count toward the total.
- split_digits uses only div/mul/sub (no mfhi/mflo/rem).

What does not work / limitations
--------------------------------
- Written for MARS. SPIM handles the random-int syscalls differently,
  so it may not run there without changes.
- A guess like 01234 cannot be entered, since read_int drops the leading
  zero and the value 1234 fails the range check. This matches the spec
  (targets never have a leading zero either).
- No replay loop. After a win or concede the program exits via
  syscall 10.
- If "Popup dialog for input syscalls" is enabled in MARS settings, the
  prompt text shows up in both the console and the input dialog. This
  is a MARS display setting, not a bug in the program.

Files
-----
quiz4      - the MIPS source
README     - this file
