import dynet as dy
import numpy as np
from baseline.utils import listify, get_model_file
from baseline.progress import create_progress_bar
from baseline.reporting import basic_reporting
from baseline.train import EpochReportingTrainer, create_trainer, lr_decay
from baseline.dy.dynety import *


class LanguageModelTrainerDynet(EpochReportingTrainer):
    def __init__(
            self,
            model,
            **kwargs
    ):
        super(LanguageModelTrainerDynet, self).__init__()
        self.model = model
        self.optimizer = optimizer(model, **kwargs)
        self.decay = lr_decay(**kwargs)
        self.global_step = 0
        self.valid_epochs = 0

    @staticmethod
    def _loss(outputs, labels):
        losses = [dy.pickneglogsoftmax_batch(out, label) for out, label in zip(outputs, labels)]
        loss = dy.mean_batches(dy.esum(losses))
        return loss

    def train(self, loader, reporting_fns, **kwargs):
        metrics = {}
        total_loss = 0.0
        iters = 0
        step = 0
        initial_state = None
        for batch_dict in loader:
            dy.renew_cg()
            self.optimizer.learning_rate = self.decay(self.global_step)
            input_, labels = self.model.make_input(batch_dict)
            output, initial_state = self.model.forward(input_, initial_state)
            loss = self._loss(output, labels)
            loss_val = loss.npvalue().item()
            total_loss += loss_val
            initial_state = [x.npvalue() for x in initial_state]
            loss.backward()
            self.optimizer.update()

            iters += len(labels)
            step += 1
            self.global_step += 1

            if step % 500 == 0:
                print(total_loss, iters)
                metrics['avg_loss'] = total_loss / iters
                metrics['perplexity'] = np.exp(total_loss / iters)
                for reporting in reporting_fns:
                    reporting(metrics, self.global_step, 'Train')

        metrics['avg_loss'] = total_loss / iters
        metrics['perplexity'] = np.exp(total_loss / iters)
        for reporting in reporting_fns:
            reporting(metrics, self.global_step, 'Train')
        return metrics

    def test(self, loader, reporting_fns, phase, **kwargs):
        metrics = {}
        total_loss = 0.0
        iters = 0
        initial_state = None
        for batch_dict in loader:
            dy.renew_cg()
            input_, labels = self.model.make_input(batch_dict)
            output, initial_state = self.model.forward(input_, initial_state, train=False)
            loss = self._loss(output, labels)
            loss_val = loss.npvalue().item()
            total_loss += loss_val
            initial_state = [x.npvalue() for x in initial_state]

            iters += len(labels)

        if phase == 'Valid':
            self.valid_epochs += 1
            output = self.valid_epochs
        else:
            output = 0

        metrics['avg_loss'] = total_loss / iters
        metrics['perplexity'] = np.exp(total_loss / iters)
        for reporting in reporting_fns:
            reporting(metrics, output, phase)
        return metrics


def fit(model,
        ts, vs, es=None,
        epochs=5,
        do_early_stopping=True, early_stopping_metric='avg_loss',
        reporting=basic_reporting,
        **kwargs):

    patience = int(kwargs.get('patience', epochs))
    after_train_fn = kwargs.get('after_train_fn', None)

    model_file = get_model_file(kwargs, 'lm', 'dy')

    trainer = create_trainer(LanguageModelTrainerDynet, model, **kwargs)

    if do_early_stopping:
        print("Doing early stopping on [{}] with patience [{}]".format(early_stopping_metric, patience))

    reporting_fns = listify(reporting)
    print('reporting', reporting_fns)

    min_metric = 10000
    last_improved = 0

    for epoch in range(epochs):
        trainer.train(ts, reporting_fns)
        if after_train_fn is not None:
            after_train_fn(model)

        test_metrics = trainer.test(vs, reporting_fns, phase='Valid')

        if do_early_stopping is False:
            model.save(model_file)

        elif test_metrics[early_stopping_metric] < min_metric:
            last_improved = epoch
            min_metric = test_metrics[early_stopping_metric]
            print("New min {:.3f}".format(min_metric))
            model.save(model_file)

        elif (epoch - last_improved) > patience:
            print("Stopping due to persistent failures to improve")
            break

    if do_early_stopping is True:
        print('Best performance on min_metric {:.3f} at epoch {}'.format(min_metric, last_improved))

    if es is not None:
        print('Reloading best checkpoint')
        model = model.load(model_file)
        trainer.test(es, reporting_fns, phase='Test')
