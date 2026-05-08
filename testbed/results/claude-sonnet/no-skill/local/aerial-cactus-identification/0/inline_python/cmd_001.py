# extracted from `python -c` invocation
# description: Inspect data shapes and class balance


import pandas as pd
train = pd.read_csv('/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/train.csv')
print('Train shape:', train.shape)
print('Class balance:')
print(train['has_cactus'].value_counts())
print('Positive rate:', train['has_cactus'].mean())

sub = pd.read_csv('/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/sample_submission.csv')
print('Test shape:', sub.shape)

