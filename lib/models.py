import numpy as np
import scipy as sp
from sklearn.linear_model import Ridge, RidgeClassifier, LogisticRegression
from sklearn.naive_bayes import BernoulliNB, MultinomialNB, GaussianNB
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor 
from sklearn.ensemble import BaggingClassifier, BaggingRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor 
from sklearn.pipeline import Pipeline
from sklearn.feature_selection import SelectKBest
from sklearn.feature_selection import chi2
import operator
import copy
from memory_profiler import profile
from sklearn.datasets import load_svmlight_file
class MyAutoML:
    ''' Rough sketch of a class that "solves" the AutoML problem. We illustrate various type of data that will be encountered in the challenge can be handled.
         Also, we make sure that the model regularly outputs predictions on validation and test data, such that, if the execution of the program is interrupted (timeout)
         there are still results provided by the program. The baseline methods chosen are not optimized and do not provide particularly good results.
         In particular, no special effort was put into dealing with missing values and categorical variables.
         
         The constructor selects a model based on the data infoormation passed as argument. This is a form of model selection "filter".
         We anticipate that the participants may compute a wider range of statistics to perform filter model selection.
         We also anticipate that the participants will conduct cross-validation experiments to further select amoung various models
         and hyper-parameters of the model. They might walk trough "model space" systematically (e.g. with grid search), heuristically (e.g. with greedy strategies),
         or stochastically (random walks). This example does not bother doing that. We simply use a growing ensemble of models to improve predictions over time.
         
         We use ensemble methods that vote on an increasing number of classifiers. For efficiency, we use WARM START that re-uses
         already trained base predictors, when available.
         
        IMPORTANT: This is just a "toy" example:
            - if was checked only on the phase 3 data at the time of release
            - not all cases are considered
            - this could easily break on datasets from further phases (or previous phases)
            - this is very inefficient (most ensembles have no "warm start" option, hence we do a lot of unnecessary calculations)
            - there is no preprocessing
         '''
         
    def __init__(self, info, verbose=True, debug_mode=False, run_on_gpu=False):
        self.label_num=info['label_num']
        self.target_num=info['target_num']
        self.task = info['task']
        self.metric = info['metric']
        self.postprocessor = MultiLabelEnsemble(LogisticRegression(), balance=False) # To calibrate proba
        if debug_mode>=2:
            self.name = "RandomPredictor"
            self.model = RandomPredictor(self.target_num)
            self.predict_method = self.model.predict_proba 
            return
        if info['task']=='regression':
            if info['is_sparse']==True:
                self.name = "BaggingRidgeRegressor"
                self.model = BaggingRegressor(base_estimator=Ridge(), n_estimators=1, verbose=verbose, random_state=1) # unfortunately, no warm start...
                # Lukasz uses BernoulliNB() instead of Ridge()
            else:
                #self.name = "GradientBoostingRegressor"
                #self.model = GradientBoostingRegressor(n_estimators=1, verbose=verbose, warm_start = True, random_state=1)
                # There is a problem with  "GradientBoostingRegressor", which does not accept non c-contiguous arrays.     
                self.name = "RandomForestRegressor"
                self.model = RandomForestRegressor(n_estimators=1, random_state=1, warm_start = True)
            self.predict_method = self.model.predict
        else:
            if info['has_categorical']: # Out of lazziness, we do not convert categorical variables...
                self.name = "RandomForestClassifier"
                self.model = RandomForestClassifier(n_estimators=1, verbose=verbose, random_state=1) # New: warm_start = True ,now there is warm start is sklearn 0.16.1 not in here for backward compatibility
            elif info['format'] == 'sparse_binary':  
                self.name = "BaggingBernoulliNBClassifier"
                self.model = BaggingClassifier(base_estimator=BernoulliNB(), n_estimators=1, verbose=verbose, random_state=1) # unfortunately, no warm start...                          
            elif info['format'] == 'sparse': 
                self.name = "BaggingMutinomialNBClassifier"
                self.model = BaggingClassifier(base_estimator=MultinomialNB(), n_estimators=1, verbose=verbose, random_state=1) # unfortunately, no warm start...                          
            else:  
                if info['label_num']>100:
                    self.name = "BaggingGaussianNBClassifier"
                    self.model = BaggingClassifier(base_estimator=GaussianNB(), n_estimators=1, verbose=verbose, random_state=1) # unfortunately, no warm start...                          
                else:
                #self.name = "RandomForestClassifier"
                #self.model = RandomForestClassifier(n_estimators=1, verbose=verbose, warm_start = True , random_state=1) # New: now there is warm start is sklearn 0.16.1
                    self.name = "GradientBoostingClassifier"
                    self.model = GradientBoostingClassifier(n_estimators=1, verbose=verbose, random_state=1, min_samples_split=10, warm_start = False) # New bug warm start no longer works
            if info['task']=='multilabel.classification':
                self.model = MultiLabelEnsemble(self.model)
            self.predict_method = self.model.predict_proba                            

    def __repr__(self):
        return "MyAutoML : " + self.name

    def __str__(self):
        return "MyAutoML : \n" + str(self.model) 

    def fit(self, X, Y):
        self.model.fit(X,Y)
        # Train a calibration model postprocessor
        if self.task != 'regression' and self.postprocessor!=None:
            Yhat = self.predict_method(X)
            if len(Yhat.shape)==1: # IG modif Feb3 2015
                Yhat = np.reshape(Yhat,(-1,1))           
            self.postprocessor.fit(Yhat, Y)
        return self
        
    def predict(self, X):
        prediction = self.predict_method(X)
        # Calibrate proba
        if self.task != 'regression' and self.postprocessor!=None:          
            prediction = self.postprocessor.predict_proba(prediction)
        # Keep only 2nd column because the second one is 1-first    
        if self.target_num==1 and len(prediction.shape)>1 and prediction.shape[1]>1:
            prediction = prediction[:,1]
        # Make sure the normalization is correct
        if self.task=='multiclass.classification':
            eps = 1e-15
            norma = np.sum(prediction, axis=1)
            for k in range(prediction.shape[0]):
                prediction[k,:] /= sp.maximum(norma[k], eps)  
        return prediction
        

class MultiLabelEnsemble:
    ''' MultiLabelEnsemble(predictorInstance, balance=False)
        Like OneVsRestClassifier: Wrapping class to train multiple models when 
        several objectives are given as target values. Its predictor may be an ensemble.
        This class can be used to create a one-vs-rest classifier from multiple 0/1 labels
        to treat a multi-label problem or to create a one-vs-rest classifier from
        a categorical target variable.
        Arguments:
            predictorInstance -- A predictor instance is passed as argument (be careful, you must instantiate
        the predictor class before passing the argument, i.e. end with (), 
        e.g. LogisticRegression().
            balance -- True/False. If True, attempts to re-balance classes in training data
            by including a random sample (without replacement) s.t. the largest class has at most 2 times
        the number of elements of the smallest one.
        Example Usage: mymodel =  MultiLabelEnsemble (GradientBoostingClassifier(), True)'''
	
    def __init__(self, predictorInstance, balance=False):
        self.predictors = [predictorInstance]
        self.n_label = 1
        self.n_target = 1
        self.n_estimators =  1 # for predictors that are ensembles of estimators
        self.balance=balance
        
    def __repr__(self):
        return "MultiLabelEnsemble"

    def __str__(self):
        return "MultiLabelEnsemble : \n" + "\tn_label={}\n".format(self.n_label) + "\tn_target={}\n".format(self.n_target) + "\tn_estimators={}\n".format(self.n_estimators) + str(self.predictors[0])
	
    def fit(self, X, Y):
        if len(Y.shape)==1: 
            Y = np.array([Y]).transpose() # Transform vector into column matrix
            # This is NOT what we want: Y = Y.reshape( -1, 1 ), because Y.shape[1] out of range
        self.n_target = Y.shape[1]                 # Num target values = num col of Y
        self.n_label = len(set(Y.ravel()))         # Num labels = num classes (categories of categorical var if n_target=1 or n_target if labels are binary )
        # Create the right number of copies of the predictor instance
        if len(self.predictors)!=self.n_target:
            predictorInstance = self.predictors[0]
            self.predictors = [predictorInstance]
            for i in range(1,self.n_target):
                self.predictors.append(copy.copy(predictorInstance))
        # Fit all predictors
        for i in range(self.n_target):
            # Update the number of desired prodictos
            if hasattr(self.predictors[i], 'n_estimators'):
                self.predictors[i].n_estimators=self.n_estimators
            # Subsample if desired
            if self.balance:
                pos = Y[:,i]>0
                neg = Y[:,i]<=0
                if sum(pos)<sum(neg): 
                    chosen = pos
                    not_chosen = neg
                else: 
                    chosen = neg
                    not_chosen = pos
                num = sum(chosen)
                idx=filter(lambda(x): x[1]==True, enumerate(not_chosen))
                idx=np.array(zip(*idx)[0])
                np.random.shuffle(idx)
                chosen[idx[0:min(num, len(idx))]]=True
                # Train with chosen samples            
                self.predictors[i].fit(X[chosen,:],Y[chosen,i])
            else:
                self.predictors[i].fit(X,Y[:,i])
        return
		
    def predict_proba(self, X):
        if len(X.shape)==1: # IG modif Feb3 2015
            X = np.reshape(X,(-1,1))   
        prediction = self.predictors[0].predict_proba(X)
        if self.n_label==2:                 # Keep only 1 prediction, 1st column = (1 - 2nd column)
            prediction = prediction[:,1]
        for i in range(1,self.n_target): # More than 1 target, we assume that labels are binary
            new_prediction = self.predictors[i].predict_proba(X)[:,1]
            prediction = np.column_stack((prediction, new_prediction))
        return prediction
		
class RandomPredictor:
    ''' Make random predictions.'''
	
    def __init__(self, target_num):
        self.target_num=target_num
        self.n_estimators =  1
        return
        
    def __repr__(self):
        return "RandomPredictor"

    def __str__(self):
        return "RandomPredictor"
	
    def fit(self, X, Y):
        if len(Y.shape)>1:
            assert(self.target_num==Y.shape[1])
        return self
		
    def predict_proba(self, X):
        prediction = np.random.rand(X.shape[0],self.target_num)
        return prediction			
