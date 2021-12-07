import sys
import os

sys.path.append('{}/src/model/utils/nlc2cmd/utils/metric'.format(os.path.abspath(os.getcwd())))
sys.path.append('{}/src/model/utils/nlc2cmd/tellina-baseline/src'.format(os.path.abspath(os.getcwd())))

from submission_code.encoder_decoder.slot_filling import slot_filler_type_match
import argparse
import time
import json
from datetime import datetime
from utils.dataset import Nlc2CmdDS
from utils.dataloaders import Nlc2CmdDL
from utils.metric_utils import compute_metric
from src.model import data_process
import src.model.predict as predictor
from submission_code.bashlint.bash import argument_types
from bashlint.data_tools import bash_tokenizer


def get_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument('--annotation_filepath', type=str, default='test_data.json')
    parser.add_argument('--params_filepath', type=str)
    parser.add_argument('--output_folderpath', type=str)
    parser.add_argument('--mode', type=str, required=False, default='eval')
    parser.add_argument('--data_dir', type=str, default='src/data')
    parser.add_argument('--data_file', type=str, default='nl2bash-data.json')
    parser.add_argument('--model_dir', type=str, default='src/model/run')
    parser.add_argument('--model_file', nargs='*', default=['model_step_2500.pt'], type=str)
    parser.add_argument('--sentence', type=str)
    return parser


def get_dataloader(annotation_filepath):
    nlc2cmd_ds = Nlc2CmdDS(annotation_filepath)
    nlc2cmd_dl = Nlc2CmdDL(nlc2cmd_ds, batchsize=8, shuffle=True)
    return iter(nlc2cmd_dl)


def get_params(params_filepath):
    with open(params_filepath, 'r') as f:
        params = json.load(f)
    return params


def validate_predictions(predicted_cmds, predicted_confds, n_batch, result_cnt):
    assert len(predicted_cmds) == n_batch, \
        f'{len(predicted_cmds)} commands predicted for {n_batch} invocations'

    assert len(predicted_confds) == n_batch, \
        f'{len(predicted_confds)} confidences predicted for {n_batch} invocations'

    for i in range(n_batch):
        assert 1 <= len(predicted_cmds[i]) <= result_cnt, \
            f'{len(predicted_cmds[i])} commands predicted for an invocations. Expected between 1 and  {result_cnt}'

        assert 1 <= len(predicted_confds[i]) <= result_cnt, \
            f'{len(predicted_confds[i])} confidences predicted for an invocations. Expected between 1 and {result_cnt}'

        assert not (False in [0.0 <= x <= 1.0 for x in predicted_confds[i]]), \
            f'Confidence value beyond the allowed range of [0.0, 1.0] found in predictions'


def get_predictions(nlc2cmd_dl, model_dir, model_file):
    result_cnt = 5
    i = 0
    ground_truths = []
    predicted_cmds, predicted_confds = [], []

    for invocations, cmds in nlc2cmd_dl:
        batch_predicted_cmds, batch_predicted_confd = predictor.predict(invocations,
                                                                        model_dir,
                                                                        model_file,
                                                                        result_cnt=result_cnt)
        validate_predictions(batch_predicted_cmds, batch_predicted_confd, len(invocations), result_cnt)

        ground_truths.extend(cmds)
        predicted_cmds.extend(batch_predicted_cmds)
        predicted_confds.extend(batch_predicted_confd)

        if i % 15 == 0:
            now = datetime.now().strftime('%d/%m %H:%M:%S')
            print(f'\t{now} :: {i} batches predicted')
        i += 1

    return ground_truths, predicted_cmds, predicted_confds


def get_score(prediction_scores):
    score = -1.0
    if len(prediction_scores) == 0:
        return score

    has_positive_score = True in [x > 0 for x in prediction_scores]

    if has_positive_score:
        score = max(prediction_scores)
    else:
        score = sum(prediction_scores) / float(len(prediction_scores))

    return score


def compute_score(ground_truths, predicted_cmds, predicted_confds, metric_params):
    prediction_scores = []

    for grnd_truth_cmd in ground_truths:
        for i, predicted_cmd in enumerate(predicted_cmds):

            if predicted_cmd is None or len(predicted_cmd) == 0:
                continue

            predicted_confidence = predicted_confds[i]
            pair_score = compute_metric(predicted_cmd, predicted_confidence, grnd_truth_cmd, metric_params)
            prediction_scores.append(pair_score)

    score = get_score(prediction_scores)

    print('-' * 50)
    print(f'Ground truth: {ground_truths}')
    print(f'Predictions: {predicted_cmds}')
    print(f'Score: {score}')

    return score


def evaluate_model(annotation_filepath, params_filepath, model_dir, model_file):
    try:
        params = get_params(params_filepath)

        nlc2cmd_dl = get_dataloader(annotation_filepath)

        stime = time.time()
        fn_return = get_predictions(nlc2cmd_dl, model_dir, model_file)
        total_time_taken = time.time() - stime

        ground_truths, predicted_cmds, predicted_confds = fn_return
        n = len(ground_truths)

        print('----------------------- Predictions -----------------------')

        scores = [
            compute_score(ground_truths[i], predicted_cmds[i], predicted_confds[i], params)
            for i in range(n)
        ]

        print(f'sum: {sum(scores)}, n: {n}')
        print('----------------------- Predictions -----------------------')

        mean_score = sum(scores) / float(n)
        time_taken = total_time_taken / float(n)

        result = {
            'status': 'success',
            'time_taken': time_taken,
            'score': mean_score
        }

    except Exception as err:
        result = {
            'status': 'error',
            'error_message': str(err)
        }

    return result


def single(sentence, model_dir, model_file):
    commands, confidences, new_invocations, placeholders = predictor.predict([sentence],
                                model_dir,
                                model_file,
                                result_cnt=1)

    new_cmd = replace_placeholders(commands, placeholders)
    if len(new_cmd) > 0:
        utility_parts = new_cmd[0].split('_')
        new_cmd = utility_parts + new_cmd[1:]

    new_cmd = ['jfrog'] + new_cmd
    print(f"Result={' '.join(new_cmd)}")
    print(f"Result_pre_process={commands[0][0]}")
    print(f"NewInvocation={','.join(new_invocations[0])}")
    print(f"Placeholders={','.join([','.join([str(v[0]),v[1][0],v[1][1]]) for v in placeholders[0][0].items()])}")


def replace_placeholders(commands, placeholders):
    parts = commands[0][0].split(' ')
    new_cmd = []
    used_fillers = set()
    for p in parts:
        p_tran = p
        if p in argument_types:
            for k, v in placeholders[0][0].items():
                if k not in used_fillers and slot_filler_type_match(p, v[1]):
                    used_fillers.add(k)
                    p_tran = v[0]
                    break
        new_cmd.append(p_tran)
    return new_cmd


if __name__ == '__main__':
    parser = get_parser()
    args = parser.parse_args()

    if args.mode == 'eval':
        result = evaluate_model(args.annotation_filepath, args.params_filepath, args.model_dir, args.model_file)
    elif args.mode == 'train':
        pass
    elif args.mode == 'preprocess':
        data_process.preprocess(args.data_dir, args.data_file)
    elif args.mode == 'single':
        single(args.sentence, args.model_dir, args.model_file)
    if args.mode in ['eval', 'energy']:
        os.makedirs(args.output_folderpath, exist_ok=True)
        with open(os.path.join(args.output_folderpath, 'result.json'), 'w') as f:
            json.dump(result, f)
