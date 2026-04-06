Mastermind - README
Flynn

What it does
------------
This program runs a 5-digit version of the Bagels/Fermi/Pico Mastermind game in MARS. 
The computer generates a random 5-digit number where all digits are unique and the 
first digit is not zero. The player keeps entering guesses until they either guess 
correctly or choose to quit.

After each guess, the program prints feedback:
- Fermi: digit is correct and in the correct position
- Pico: digit exists in the number but is in the wrong position
- Bagels: no digits from the guess appear in the target

The game ends when the player guesses the number exactly or enters 0 to concede. 
In both cases, the program prints the total number of guesses.

What works
----------
- Random target generation using syscall 42
- Ensures all digits are unique using a retry loop
- First digit is always between 1 and 9
- Input is handled with a single read_int per guess
- Correctly calculates Fermi, Pico, and Bagels feedback
- Detects a win when all 5 digits match in position
- Tracks and prints the number of guesses
- Allows the user to concede by entering 0
- Input validation:
  * Rejects numbers outside 10000–99999
  * Rejects guesses with duplicate digits
  * Invalid guesses print an error message and are not counted

Limitations
-----------
- No replay feature after the game ends
- Program exits immediately after a win or concede
