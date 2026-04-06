Mastermind - README
Flynn

#What it does

The program is a 5 digit version of Mastermind. 
The program generates a random 5-digit target where all digits are unique and
the leading digit is nonzero. The player enters guesses as a single
5-digit integer. After each guess the program responds :
     Fermi  - digit correct and in correct position
     Pico   - digit correct but in wrong position
     Bagels - no digits match at all
 The game ends when all the numbers match correctly, or if the player
  surrenders by entering 0000 (read as integer 0). In either case the#   program prints the number of guesses used.

# What works

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

# Limitations

- No replay feature after the game ends
- Program exits immediately after a win or concede
