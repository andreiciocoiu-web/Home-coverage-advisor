"""Creeaza un utilizator si afiseaza tokenul de acces (o singura data).

Utilizare: python -m scripts.create_user <nume> <viewer|analyst|reviewer|admin>
Roluri: viewer (vede documente), analyst (analize, export), reviewer (corecteaza metadate,
feedback, propune metodologie), admin (aprobari, audit, surse locale, retentie).
"""
import sys

from advisor.security import ROLES, create_user

if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ROLES:
        print(__doc__)
        sys.exit(1)
    token = create_user(sys.argv[1], sys.argv[2])
    print(f"Utilizator {sys.argv[1]} ({sys.argv[2]}) creat. Token (se afiseaza o singura data):\n{token}")
