Mastermind - README
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

What works
----------
- Computer generates the target using syscall 42 (random int range),
  with uniqueness enforced by a retry loop and the first digit forced
  into 1-9.
- Single read_int per guess (no digit-by-digit input).
- Fermi/Pico/Bagels scoring for 5-digit guesses.
- Win detection when all 5 digits match in place; prints the guess count.
- Surrender button on input 0: reveals the target and prints the guess count.
- Input validation:
    * rejects values outside 10000-99999
    * rejects guesses with duplicate digits
  Invalid guesses prints error and do not count

What does not work / limitations
--------------------------------
- No replay loop. After a win or concede the program exits.

