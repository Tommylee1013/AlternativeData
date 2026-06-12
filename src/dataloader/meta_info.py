import pandas as pd

from pathlib import Path # Path lib 필요

# Const Values

OUTPUT_PATH = Path(
    'data/metadata.xlsx'
)

def get_master_table() :
    data = pd.read_excel(OUTPUT_PATH)
    return data

if __name__ == '__main__' :
    data = get_master_table()
    print(data)
