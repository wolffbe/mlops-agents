# extracted from `python -c` invocation
# description: Check PyTorch availability

import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.cuda.is_available())
