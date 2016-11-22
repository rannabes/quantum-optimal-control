import numpy as np
import tensorflow as tf
from Analysis import Analysis
import os
import time
from scipy.optimize import minimize

from helper_functions.datamanagement import H5File


class run_session:
    def __init__(self, tfs,graph,conv,sys_para,method,show_plots=True,single_simulation = False,use_gpu =True):
        self.tfs=tfs
        self.graph = graph
        self.conv = conv
        self.sys_para = sys_para
        self.update_step = conv.update_step
        self.iterations = 0
        self.method = method
        self.show_plots = show_plots
        self.target = False
        if not use_gpu:
            config = tf.ConfigProto(device_count = {'GPU': 0})
        else:
            config = None
        
        with tf.Session(graph=graph, config = config) as self.session:
            
            tf.initialize_all_variables().run()

            print "Initialized"
 
            if self.method != 'Adam': #Any BFGS scheme
                self.bfgs_optimize(method=self.method)
                
            if self.method =='Adam':
                self.start_adam_optimizer()             
                
                  
    def start_adam_optimizer(self):
        # adam optimizer  
        self.start_time = time.time()
        while True:
            learning_rate = float(self.conv.rate) * np.exp(-float(self.iterations) / self.conv.learning_rate_decay)
            self.feed_dict = {self.tfs.learning_rate: learning_rate}

            self.g_squared, _, self.l, self.rl, self.metric = self.session.run(
                [self.tfs.grad_squared, self.tfs.optimizer, self.tfs.loss, self.tfs.reg_loss, self.tfs.unitary_scale], feed_dict=self.feed_dict)

            self.end = False

            if (self.l < self.conv.conv_target) or (self.g_squared < self.conv.min_grad) \
                    or (self.iterations >= self.conv.max_iterations):
                self.end = True

            if (self.iterations % self.conv.update_step == 0) or self.end:
                self.save_and_display()

            if self.end:
                self.get_end_results()
                break

            self.iterations += 1
            
    
    def get_end_results(self):
        # get optimized pulse and propagation
        
        # get and save inter vects
        if self.sys_para.show_plots == False:
            self.anly.get_inter_vecs()
        
        self.uks = self.Get_uks()
        if not self.sys_para.state_transfer:
            self.Uf = self.anly.get_final_state()
        else:
            self.Uf = []
        
    
    
    def Get_uks(self): 
        # to get the pulse amplitudes
        uks = self.anly.get_ops_weight()
        for ii in range (len(uks)):
            uks[ii] = self.sys_para.ops_max_amp[ii]*uks[ii]
        return uks    

    
    
    def get_error(self,uks):
        #get error and gradient for scipy bfgs:
        self.session.run(self.tfs.ops_weight_base.assign(uks))

        g,l,rl,metric,g_squared = self.session.run([self.tfs.grad_pack, self.tfs.loss, self.tfs.reg_loss, self.tfs.unitary_scale, self.tfs.grad_squared])
        
        final_g = np.transpose(np.reshape(g,(len(self.sys_para.ops_c)*self.sys_para.steps)))

        return l,rl,final_g,metric, g_squared
    
    
    def save_and_display(self):
        # save and display of simulation results
        elapsed = time.time() - self.start_time
        if self.sys_para.save:
            
            self.anly = Analysis(self.sys_para, self.tfs.final_state, self.tfs.ops_weight, self.tfs.unitary_scale,
                                 self.tfs.inter_vecs)

            with H5File(self.sys_para.file_path) as hf:
                hf.append('error', np.array(self.l))
                hf.append('reg_error', np.array(self.rl))
                hf.append('uks', np.array(self.Get_uks()))
                hf.append('iteration', np.array(self.iterations))
                hf.append('run_time', np.array(elapsed))
                hf.append('unitary_scale', np.array(self.metric))

        if self.iterations == 0:
            self.start_time = time.time()
        if self.show_plots and (not self.sys_para.save):
            self.anly = Analysis(self.sys_para, self.tfs.final_state, self.tfs.ops_weight, self.tfs.unitary_scale,
                                 self.tfs.inter_vecs)
        if self.show_plots:

            self.conv.update_convergence(self.l, self.rl, self.anly, True)
        else:

            print 'Error = :%1.2e; Runtime: %.1fs; Iterations = %d, grads =  %10.3e, unitary_metric = %.5f' % (
            self.l, elapsed, self.iterations, self.g_squared, self.metric)
    
    
    def minimize_opt_fun(self,x):
        # minimization function called by scipy in each iteration
        self.l,self.rl,self.grads,self.metric,self.g_squared=self.get_error(np.reshape(x,(len(self.sys_para.ops_c),len(x)/len(self.sys_para.ops_c))))
        
        if self.l <self.conv.conv_target :
            self.conv_time = time.time()-self.start_time
            self.conv_iter = self.iterations
            self.end = True
            print 'Target fidelity reached'
            self.grads= 0*self.grads # set zero grads to terminate the scipy optimization
        if self.iterations % self.update_step == 0 or self.end :
            self.save_and_display()
        
        self.iterations+=1
        
        if self.method == 'L-BFGS-B':
            return np.float64(self.rl),np.float64(np.transpose(self.grads))
        else:
            return self.rl,np.reshape(np.transpose(self.grads),[len(np.transpose(self.grads))])

    
    def bfgs_optimize(self, method='L-BFGS-B',jac = True, options=None):
        # scipy optimizer
        self.conv.reset_convergence()
        self.first=True
        self.conv_time = 0.
        self.conv_iter=0
        self.end=False
        print "Starting " + self.method + " Optimization"
        self.start_time = time.time()
        
        x0 = self.sys_para.ops_weight_base
        options={'maxfun' : self.conv.max_iterations,'gtol': self.conv.min_grad, 'disp':False,'maxls': 40}
        
        res = minimize(self.minimize_opt_fun,x0,method=method,jac=jac,options=options)

        uks=np.reshape(res['x'],(len(self.sys_para.ops_c),len(res['x'])/len(self.sys_para.ops_c)))

        print self.method + ' optimization done'
        
        g, l,rl = self.session.run([self.tfs.grad_squared, self.tfs.loss, self.tfs.reg_loss])
            
        if self.sys_para.show_plots == False:
            self.anly = Analysis(self.sys_para,self.tfs.final_state,self.tfs.ops_weight,self.tfs.unitary_scale,self.tfs.inter_vecs)
            self.conv.update_convergence(l,rl,self.anly,True)
            print res.message
            print("Error = %1.2e" %l)
            print ("Total time is " + str(time.time() - self.start_time))
            
        self.get_end_results()          

    
        
