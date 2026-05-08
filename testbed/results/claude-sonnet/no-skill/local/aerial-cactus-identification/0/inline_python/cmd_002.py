# extracted from `python -c` invocation
# description: Check image dimensions and format


from PIL import Image
import numpy as np
# Check a sample image
img = Image.open('/Users/wolffbe/workspace/banter/testbed/.local/mle-bench-data/aerial-cactus-identification/prepared/public/2de8f189f1dce439766637e75df0ee27.jpg')
print('Image size:', img.size, 'Mode:', img.mode)
print('Array shape:', np.array(img).shape)

