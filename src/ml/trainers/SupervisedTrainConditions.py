###################################

# IT - PreEpiSeizures

# Package: ml
# File: ModelTrainer
# Description: Class to train machine learning models

# Contributors: João Saraiva
# Created: 04/06/2022

###################################


class SupervisedTrainConditions():
    def __init__(self, **conditions):
        '''if len(conditions) != 0:
            self.n_epochs = conditions['n_epochs'] if 'n_epochs' in conditions else None
            self.batch_size = conditions['batch_size'] if 'batch_size' in conditions else None
            self.loss_function = conditions['loss_function'] if 'loss_function' in conditions else None
            self.train_size = conditions['train_size'] if 'train_size' in conditions else None
            self.test_size = conditions['test_size'] if 'test_size' in conditions else None
            self.shuffle = conditions['shuffle'] if 'shuffle' in conditions else None
            '''
        self.__conditions = conditions

        # Assertions
        if 'train_size' not in self.__conditions and 'test_size' not in self.__conditions:
            raise AttributeError("Specify at least 'train_size' or 'test_size'.")

    def __getitem__(self, item):
        return self.__conditions[item]

    @property
    def parameters(self):
        return self.__conditions