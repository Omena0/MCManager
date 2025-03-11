
class Validators:
    number = lambda x: str(x).isnumeric()
    alphanumeric = lambda x: str(x).isalnum()
    alphabetic = lambda x: str(x).isalpha()
    decimal = lambda x: str(x).replace('.', '', 1).isdigit()
