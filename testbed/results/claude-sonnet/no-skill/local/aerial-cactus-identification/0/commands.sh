# Bash commands extracted from Claude Code stream log

# List workspace directory contents
ls /Users/wolffbe/workspace/banter/testbed/results/claude-sonnet/no-skill/local/aerial-cactus-identification/0/

# Explore dataset directory structure
ls /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/ | head -5
find /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/ -maxdepth 1 -type f | head -10
ls -la /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/../ 2>/dev/null || ls -la /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/

# Find CSV files and directories in dataset
find /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/ -name "*.csv" 2>/dev/null
find /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/ -type d 2>/dev/null

# Inspect CSV files
head -5 /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/train.csv
echo "---"
head -5 /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/sample_submission.csv
echo "---"
wc -l /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/train.csv
wc -l /Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/sample_submission.csv

# Inspect data shapes and class balance
python3 -c "
import pandas as pd
train = pd.read_csv('/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/train.csv')
print('Train shape:', train.shape)
print('Class balance:')
print(train['has_cactus'].value_counts())
print('Positive rate:', train['has_cactus'].mean())

sub = pd.read_csv('/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/sample_submission.csv')
print('Test shape:', sub.shape)
"


# Check image dimensions and format
python3 -c "
from PIL import Image
import numpy as np
# Check a sample image
img = Image.open('/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/2de8f189f1dce439766637e75df0ee27.jpg')
print('Image size:', img.size, 'Mode:', img.mode)
print('Array shape:', np.array(img).shape)
"


# Check PyTorch availability
python3 -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"
python3 -c "import torchvision; print('torchvision:', torchvision.__version__)"

# Write training script
cat > /Users/wolffbe/workspace/banter/testbed/results/claude-sonnet/no-skill/local/aerial-cactus-identification/0/train.py << 'EOF'
import os, sys, time
import numpy as np
import pandas as pd
from PIL import Image
from sklearn.model_selection import train_test_split

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms as transforms
from sklearn.metrics import roc_auc_score

DATA_DIR = "/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public"
WORKSPACE = "/Users/wolffbe/workspace/banter/testbed/results/claude-sonnet/no-skill/local/aerial-cactus-identification/0"

print("Loading data...")
train_df = pd.read_csv(os.path.join(DATA_DIR, "train.csv"))
test_df = pd.read_csv(os.path.join(DATA_DIR, "sample_submission.csv"))

print(f"  Train: {len(train_df)} | Test: {len(test_df)}")
print(f"  Positive rate: {train_df['has_cactus'].mean():.3f}")

# Split train/val
train_data, val_data = train_test_split(train_df, test_size=0.15, random_state=42, stratify=train_df['has_cactus'])
print(f"  Train split: {len(train_data)} | Val split: {len(val_data)}")

class CactusDataset(Dataset):
    def __init__(self, df, data_dir, transform=None, has_labels=True):
        self.df = df.reset_index(drop=True)
        self.data_dir = data_dir
        self.transform = transform
        self.has_labels = has_labels

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        img_id = self.df.loc[idx, 'id']
        img_path = os.path.join(self.data_dir, img_id)
        img = Image.open(img_path).convert('RGB')
        if self.transform:
            img = self.transform(img)
        if self.has_labels:
            label = float(self.df.loc[idx, 'has_cactus'])
            return img, label
        return img

# Transforms
train_transform = transforms.Compose([
    transforms.RandomHorizontalFlip(),
    transforms.RandomVerticalFlip(),
    transforms.RandomRotation(15),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

val_transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

train_ds = CactusDataset(train_data, DATA_DIR, transform=train_transform)
val_ds = CactusDataset(val_data, DATA_DIR, transform=val_transform)
test_ds = CactusDataset(test_df, DATA_DIR, transform=val_transform, has_labels=False)

train_loader = DataLoader(train_ds, batch_size=64, shuffle=True, num_workers=0)
val_loader = DataLoader(val_ds, batch_size=64, shuffle=False, num_workers=0)
test_loader = DataLoader(test_ds, batch_size=64, shuffle=False, num_workers=0)

# Small CNN for 32x32 images
class CactusCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.Conv2d(32, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.MaxPool2d(2),  # 16x16
            nn.Dropout2d(0.1),

            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.MaxPool2d(2),  # 8x8
            nn.Dropout2d(0.1),

            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
            nn.Conv2d(128, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
            nn.MaxPool2d(2),  # 4x4
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 4 * 4, 256),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(256, 1)
        )

    def forward(self, x):
        x = self.features(x)
        return self.classifier(x).squeeze(1)

device = torch.device('cpu')
model = CactusCNN().to(device)
print(f"\nModel parameters: {sum(p.numel() for p in model.parameters()):,}")

criterion = nn.BCEWithLogitsLoss()
optimizer = optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=20)

print("\nTraining...")
best_auc = 0.0
best_state = None

for epoch in range(1, 21):
    # Train
    model.train()
    train_loss = 0.0
    for imgs, labels in train_loader:
        imgs, labels = imgs.to(device), labels.to(device)
        optimizer.zero_grad()
        logits = model(imgs)
        loss = criterion(logits, labels)
        loss.backward()
        optimizer.step()
        train_loss += loss.item() * len(imgs)
    train_loss /= len(train_ds)
    scheduler.step()

    # Validate
    model.eval()
    val_preds, val_labels = [], []
    with torch.no_grad():
        for imgs, labels in val_loader:
            imgs = imgs.to(device)
            logits = model(imgs)
            probs = torch.sigmoid(logits).cpu().numpy()
            val_preds.extend(probs)
            val_labels.extend(labels.numpy())
    auc = roc_auc_score(val_labels, val_preds)

    if auc > best_auc:
        best_auc = auc
        best_state = {k: v.clone() for k, v in model.state_dict().items()}

    print(f"  Epoch {epoch:02d} | loss={train_loss:.4f} | val_auc={auc:.4f} | best={best_auc:.4f}")

print(f"\nBest val AUC: {best_auc:.4f}")

# Generate test predictions
print("\nGenerating test predictions...")
model.load_state_dict(best_state)
model.eval()
all_preds = []
with torch.no_grad():
    for imgs in test_loader:
        imgs = imgs.to(device)
        logits = model(imgs)
        probs = torch.sigmoid(logits).cpu().numpy()
        all_preds.extend(probs)

submission = pd.DataFrame({'id': test_df['id'], 'has_cactus': all_preds})
submission.to_csv(os.path.join(WORKSPACE, "submission.csv"), index=False)
print(f"Submission saved: {len(submission)} rows")
print("Done.")
EOF

# Run training script
python3 -u train.py 2>&1 | tee train.log

# Verify submission file
head -5 /Users/wolffbe/workspace/banter/testbed/results/claude-sonnet/no-skill/local/aerial-cactus-identification/0/submission.csv
echo "---"
wc -l /Users/wolffbe/workspace/banter/testbed/results/claude-sonnet/no-skill/local/aerial-cactus-identification/0/submission.csv
python3 -c "
import pandas as pd
sub = pd.read_csv('submission.csv')
print('Shape:', sub.shape)
print('Columns:', list(sub.columns))
print('Prob range:', sub['has_cactus'].min(), '-', sub['has_cactus'].max())
print('Mean pred:', sub['has_cactus'].mean())
"

