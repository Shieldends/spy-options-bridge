from pathlib import Path 
p=Path('main.py') 
L=p.read_text(encoding='utf-8').splitlines() 
import sys 
[sys.stdout.write('%%4d %%s\n'%%(i+1,L[i])) for i in range(1735,1795)] 
