import os
import ast

import pandas as pd
import numpy as np

import albumentations
import argparse
import torch
import torchvision
import torch.nn as nn
import torch.nn.functional as F

from sklearn import metrics
from sklearn.model_selection import train_test_split
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

from wtfml.engine import RCNNEngine
from wtfml.data_loaders.image import RCNNLoader


def format_prediction_string(boxes, scores):
    # function taken from: https://www.kaggle.com/arunmohan003/fasterrcnn-using-pytorch-baseline
    pred_strings = []
    for j in zip(scores, boxes):
        pred_strings.append("{0:.4f} {1} {2} {3} {4}".format(j[0], j[1][0], j[1][1], j[1][2], j[1][3]))

    return " ".join(pred_strings)


def collate_fn(batch):
    return tuple(zip(*batch))


class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.base_model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
        in_features = self.base_model.roi_heads.box_predictor.cls_score.in_features
        self.base_model.roi_heads.box_predictor = FastRCNNPredictor(in_features, 2)

    def forward(self, images, targets):
        if targets is None:
            return self.base_model(images, targets)
        else:
            output = self.base_model(images, targets)
            if isinstance(output, list):
                return output
            loss = sum(loss for loss in output.values())
            return loss


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data_path", type=str,
    )
    parser.add_argument(
        "--device", type=str,
    )
    parser.add_argument(
        "--epochs", type=int,
    )
    args = parser.parse_args()

    df = pd.read_csv(os.path.join(args.data_path, "train.csv"))
    df.bbox = df.bbox.fillna("[0, 0, 10, 10]")
    df.bbox = df.bbox.apply(ast.literal_eval)
    df = df.groupby('image_id')['bbox'].apply(list).reset_index(name='bboxes')
    
    images = df.image_id.values.tolist()
    images = [os.path.join(args.data_path, "train", i + ".jpg") for i in images]
    targets = df.bboxes.values

    model = Model()
    model.to(args.device)

    mean = (0.485, 0.456, 0.406)
    std = (0.229, 0.224, 0.225)
    aug = albumentations.Compose(
        [albumentations.Normalize(mean, std, max_pixel_value=255.0, always_apply=True)]
    )

    train_images, valid_images, train_targets, valid_targets = train_test_split(
        images, targets
    )

    train_dataset = RCNNLoader(
        image_paths=train_images,
        bounding_boxes=train_targets,
        resize=(1024, 1024),
        augmentations=aug,
    )

    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=4, shuffle=True, num_workers=4, collate_fn=collate_fn
    )

    optimizer = torch.optim.Adam(model.parameters(), lr=5e-4)

    for epoch in range(args.epochs):
        train_loss = RCNNEngine.train(train_loader, model, optimizer, device=args.device)
        print(
            f"Epoch={epoch}, Train Loss={train_loss}"
        )

    del df

    test_df = pd.read_csv(os.path.join(args.data_path, "sample_submission.csv"))
    test_df.loc[:, "bbox"] = ["[0, 0, 10, 10]"] * len(test_df)
    test_df.bbox = test_df.bbox.apply(ast.literal_eval)
    test_df = test_df.groupby('image_id')['bbox'].apply(list).reset_index(name='bboxes')

    images = test_df.image_id.values.tolist()
    images = [os.path.join(args.data_path, "test", i + ".jpg") for i in images]
    targets = test_df.bboxes.values

    aug = albumentations.Compose(
        [albumentations.Normalize(mean, std, max_pixel_value=255.0, always_apply=True)]
    )

    test_dataset = RCNNLoader(
        image_paths=images, bounding_boxes=targets, resize=(1024, 1024), augmentations=aug
    )
    test_loader = torch.utils.data.DataLoader(
        test_dataset, batch_size=4, shuffle=False, num_workers=4, collate_fn=collate_fn
    )

    prediction_strings = []
    predictions = RCNNEngine.predict(test_loader, model, device=args.device)
    for p in predictions:
        boxes = p['boxes'].numpy()
        scores = p['scores'].numpy()
        
        boxes = boxes[scores >= 0.5].astype(np.int32)
        scores = scores[scores >= 0.5]

        boxes[:, 2] = boxes[:, 2] - boxes[:, 0]
        boxes[:, 3] = boxes[:, 3] - boxes[:, 1]
        prediction_strings.append(format_prediction_string(boxes, scores))

    sample = pd.read_csv(os.path.join(args.data_path, "sample_submission.csv"))
    sample.loc[:, "PredictionString"] = prediction_strings
    sample.to_csv(os.path.join(args.data_path, "submission.csv"), index=False)