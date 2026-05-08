# extracted from `python -c` invocation
# description: Verify submission file


import pandas as pd
sub = pd.read_csv('submission.csv')
print('Shape:', sub.shape)
print('Columns:', list(sub.columns))
print('Prob range:', sub['has_cactus'].min(), '-', sub['has_cactus'].max())
print('Mean pred:', sub['has_cactus'].mean())

