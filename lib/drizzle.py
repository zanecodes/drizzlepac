from __future__ import division # confidence medium

import sys,os,copy
import util
import numpy as np
import pyfits
from pytools import fileutil, teal
import outputimage,wcs_functions,processInput,util
from stwcs import distortion

try:
    import cdriz
except ImportError:
    cdriz = None
    print '\n Coordinate transformation and image resampling library, cdriz, NOT found!'
    print '\n Please check the installation of this package to insure C code was built successfully.'
    raise ImportError

__taskname__ = "betadrizzle.drizzle"
_single_step_num_ = 3
_final_step_num_ = 7

#
####  User level interface to run drizzle tasks from TEAL
#
def run(configObj,wcsmap=None):
    """
    Notes
    ------
    Values for wcsmap:
        * The default transformation (wcsmap=None) will use the WCS-based C extension:
            "cdriz.DefaultWCSMapping"
        * Python WCS transformation: 
            "wcs_functions.WCSMap"
    """

    # Explicitly turn off making copies so as to not over-write any analysis
    # already performed on the data.
    configObj['workinplace'] = False

    # Define list of imageObject instances and output WCSObject instance
    # based on input paramters
    print 'Running drizzle run()...'
    imgObjList,outwcs = processInput.setCommonInput(configObj)

    # Parse out which mode is to be run: single drizzle or final drizzle
    # Call only the mode of interest
    single_step = util.getSectionName(configObj,_single_step_num_)

    if configObj[single_step]['driz_separate']:
        drizSeparate(imgObjList,outwcs,configObj,wcsmap=wcsmap)
    else:
        drizFinal(imgObjList,outwcs,configObj,wcsmap=wcsmap)


def help():
    print getHelpAsString()

def getHelpAsString():
    """
    return useful help from a file in the script directory called module.help
    """
    helpString = teal.getHelpFileAsString(__taskname__,__file__)

    return helpString


#
#### Interactive interface for running drizzle tasks separately
#
def drizzle(input=None,drizSep=False,configObj=None,wcsmap=None,editpars=False,**input_dict):
    """Perform drizzle operation on input to create output.
     The input parameters originally was a list
     of dictionaries, one for each input, that matches the
     primary parameters for an IRAF drizzle task.

     This method would then loop over all the entries in the
     list and run 'drizzle' for each entry.

    The default transformation will be a C-based WCS extension: cdriz.DefaultWCSMapping.
    The Python class WCSMap can be used instead by setting 'wcsmap=wcs_functions.WCSMap'.

    Parameters required for input in paramDict:
        build,single,units,wt_scl,pixfrac,kernel,fillval,
        rot,scale,xsh,ysh,blotnx,blotny,outnx,outny,data
    """
    if input not in [None,""]:
        input_dict["input"]=input

    if drizSep:
        input_dict["driz_separate"]=True
        input_dict["driz_combine"]=False
    else:
        input_dict["driz_separate"]=False
        input_dict["driz_combine"]=True


    # If called from interactive user-interface, configObj will not be
    # defined yet, so get defaults using EPAR/TEAL.
    #
    # Also insure that the input_dict (user-specified values) are folded in
    # with a fully populated configObj instance.
    configObj = util.getDefaultConfigObj(__taskname__,configObj,input_dict,loadOnly=(not editpars))
    if configObj is None:
        return

    if not editpars:
        run(configObj,wcsmap=wcsmap)

#
#### Top-level interface from inside MultiDrizzle
#
def drizSeparate(imageObjectList,output_wcs,configObj,wcsmap=None,procSteps=None):
    if procSteps is not None:
        procSteps.addStep('Separate Drizzle')
    
    # ConfigObj needs to be parsed specifically for driz_separate set of parameters
    single_step = util.getSectionName(configObj,_single_step_num_)
    # This can be called directly from MultiDrizle, so only execute if
    # switch has been turned on (no guarantee MD will check before calling).
    if configObj[single_step]['driz_separate']:
        paramDict = buildDrizParamDict(configObj)
        paramDict['crbit'] = None
        paramDict['proc_unit'] = 'electrons'
        paramDict['wht_type'] = None
        # Force 'build' to always be False, so that this step always generates
        # simple FITS files as output for compatibility with 'createMedian'
        paramDict['build'] = False

        print "\nUSER INPUT PARAMETERS for Separate Drizzle Step:"
        util.printParams(paramDict)

        # override configObj[build] value with the value of the build parameter
        # this is necessary in order for MultiDrizzle to always have build=False
        # for single-drizzle step when called from the top-level.
        run_driz(imageObjectList, output_wcs.single_wcs, paramDict, single=True,
                build=False, wcsmap=wcsmap)
    else:
        print 'Single drizzle step not performed.'

    if procSteps is not None:
        procSteps.endStep('Separate Drizzle')


def drizFinal(imageObjectList, output_wcs, configObj,build=None,wcsmap=None,procSteps=None):
    if procSteps is not None:
        procSteps.addStep('Final Drizzle')
    # ConfigObj needs to be parsed specifically for driz_final set of parameters
    final_step = util.getSectionName(configObj,_final_step_num_)
    # This can be called directly from MultiDrizle, so only execute if
    # switch has been turned on (no guarantee MD will check before calling).
    if configObj[final_step]['driz_combine']:
        paramDict = buildDrizParamDict(configObj,single=False)
        paramDict['crbit'] = configObj['crbit']
        paramDict['proc_unit'] = configObj['proc_unit']
        paramDict['wht_type'] = configObj[final_step]['final_wht_type']
        
        # override configObj[build] value with the value of the build parameter
        # this is necessary in order for MultiDrizzle to always have build=False
        # for single-drizzle step when called from the top-level.
        if build is None:
            build = paramDict['build']
            
        print "\nUSER INPUT PARAMETERS for Final Drizzle Step:"
        util.printParams(paramDict)

        run_driz(imageObjectList, output_wcs.final_wcs, paramDict, single=False,
                build=build, wcsmap=wcsmap)
    else:
        print 'Final drizzle step not performed.'

    if procSteps is not None:
        procSteps.endStep('Final Drizzle')

# Run 'drizzle' here...
#

def mergeDQarray(maskname,dqarr):
    """ Merge static or CR mask with mask created from DQ array on-the-fly here.
    """
    if maskname is not None and os.path.exists(maskname):
        mask = fileutil.openImage(maskname)
        maskarr = mask[0].data
        np.bitwise_and(dqarr,maskarr,dqarr)
        mask.close()

def updateInputDQArray(dqfile,dq_extn,chip, crmaskname,cr_bits_value):
    if not os.path.exists(crmaskname):
        print 'WARNING: No CR mask file found! Input DQ array not updated.'
        return
    if cr_bits_value == None:
        print 'WARNING: Input DQ array not updated!'
        return
    crmask = fileutil.openImage(crmaskname)

    if os.path.exists(dqfile):
        fullext=dqfile+"["+dq_extn+str(chip)+"]"
        infile = fileutil.openImage(fullext,mode='update')
        __bitarray = np.logical_not(crmask[0].data).astype(np.int16) * cr_bits_value
        np.bitwise_or(infile[dq_extn,chip].data,__bitarray,infile[dq_extn,chip].data)
        infile.close()
        crmask.close()

def buildDrizParamDict(configObj,single=True):
    chip_pars = ['units','wt_scl','pixfrac','kernel','fillval','bits']
    # Initialize paramDict with global parameter(s)
    paramDict = {'build':configObj['build'],'stepsize':configObj['stepsize'],'coeffs':configObj['coeffs']}

    # build appro
    if single:
        driz_prefix = 'driz_sep_'
        stepnum = 3
    else:
        driz_prefix = 'final_'
        stepnum = 7
    section_name = util.getSectionName(configObj,stepnum)
    # Copy values from configObj for the appropriate step to paramDict
    for par in chip_pars:
        if par == 'units':
            if single:
                # Hard-code single-drizzle to always returns 'cps'
                paramDict[par] = 'cps'
            else:
                paramDict[par] = configObj[section_name][driz_prefix+par]
        else:
            paramDict[par] = configObj[section_name][driz_prefix+par]

    return paramDict
def _setDefaults(configObj={}):
    """set up the default parameters to run drizzle
        build,single,units,wt_scl,pixfrac,kernel,fillval,
        rot,scale,xsh,ysh,blotnx,blotny,outnx,outny,data

        Used exclusively for unit-testing, if any are defined.
    """

    paramDict={"build":True,
              "single":True,
              "stepsize":10,
              "in_units":"cps",
              "wt_scl":1.,
              "pixfrac":1.,
              "kernel":"square",
              "fillval":999.,
              "rot":0.,
              "scale":1.,
              "xsh":0.,
              "ysh":0.,
              "blotnx":2048,
              "blotny":2048,
              "outnx":4096,
              "outny":4096,
              "data":None,
              "driz_separate":True,
              "driz_combine":False}

    if(len(configObj) !=0):
        for key in configObj.keys():
            paramDict[key]=configObj[key]

    return paramDict

def run_driz(imageObjectList,output_wcs,paramDict,single,build,wcsmap=None):
    """Perform drizzle operation on input to create output.
     The input parameters originally was a list
     of dictionaries, one for each input, that matches the
     primary parameters for an IRAF drizzle task.

     This method would then loop over all the entries in the
     list and run 'drizzle' for each entry.

    Parameters required for input in paramDict:
        build,single,units,wt_scl,pixfrac,kernel,fillval,
        rot,scale,xsh,ysh,blotnx,blotny,outnx,outny,data
    """
    # Insure that input imageObject is a list
    if not isinstance(imageObjectList, list):
        imageObjectList = [imageObjectList]

    # Create a list which points to all the chips being combined
    # by extracting all the chips from each of the input imageObjects
    #chiplist = []
    #for img in imageObjectList:
    #    for chip in range(1,img._numchips+1):
    #        chiplist.append(img._image[img.scienceExt,chip])

    #
    # Setup the versions info dictionary for output to PRIMARY header
    # The keys will be used as the name reported in the header, as-is
    #
    _versions = {'PyDrizzle':util.__version__,'PyFITS':util.__pyfits_version__,'Numpy':util.__numpy_version__}

    # Interpret input parameters for use in drizzling
    crbit = paramDict['crbit']
    bits = paramDict['bits']
    proc_units = paramDict['proc_unit']
    wht_type = paramDict['wht_type']

    # Insure that the fillval parameter gets properly interpreted for use with tdriz
    if paramDict['fillval'] in [None, '']:
        fillval = 'INDEF'
    else:
        fillval = str(paramDict['fillval'])

    # Set sub-sampling rate for drizzling
    #stepsize = 2.0
    stepsize = paramDict['stepsize']
    print '  **Using sub-sampling value of ',stepsize,' for kernel ',paramDict['kernel']

    outwcs = copy.deepcopy(output_wcs)
    
    # Check for existance of output file.
    if single == False and build == True and fileutil.findFile(imageObjectList[0].outputNames['outFinal']):
        print 'Removing previous output product...'
        os.remove(imageObjectList[0].outputNames['outFinal'])
    
    # print out parameters being used for drizzling
    print "Running Drizzle to create output frame with WCS of: "
    output_wcs.printwcs()
    print '\n'

    # Set parameters for each input and run drizzle on it here.
    #
    # Perform drizzling...
    #

    numctx = 0
    for img in imageObjectList:
        numctx += img._nmembers
    _numctx = {'all':numctx}

    #            if single:
    # Determine how many chips make up each single image
    for img in imageObjectList:
        for chip in img.returnAllChips(extname=img.scienceExt):
            plsingle = chip.outputNames['outSingle']
            if _numctx.has_key(plsingle): _numctx[plsingle] += 1
            else: _numctx[plsingle] = 1
    #
    # A image buffer needs to be setup for converting the input
    # arrays (sci and wht) from FITS format to native format
    # with respect to byteorder and byteswapping.
    # This buffer should be reused for each input.
    #
    _outsci = np.zeros((output_wcs.naxis2,output_wcs.naxis1),dtype=np.float32)
    _outwht = np.zeros((output_wcs.naxis2,output_wcs.naxis1),dtype=np.float32)

    # Compute how many planes will be needed for the context image.
    _nplanes = int((_numctx['all']-1) / 32) + 1
    # For single drizzling or when context is turned off,
    # minimize to 1 plane only...
    if single or imageObjectList[0][1].outputNames['outContext'] in [None,'',' ']:
        _nplanes = 1

    # Always initialize context images to a 3-D array
    # and only pass the appropriate plane to drizzle as needed
    _outctx = np.zeros((_nplanes,output_wcs.naxis2,output_wcs.naxis1),dtype=np.int32)

    # Keep track of how many chips have been processed
    # For single case, this will determine when to close
    # one product and open the next.
    _numchips = 0
    _nimg = 0
    _hdrlist = []

    for img in imageObjectList:        
        for chip in img.returnAllChips(extname=img.scienceExt):

            native_units = img.native_units
            # Look for sky-subtracted product.
            if os.path.exists(chip.outputNames['outSky']):
                chipextn = '['+chip.header['extname']+','+str(chip.header['extver'])+']'
                _expname = chip.outputNames['outSky']+chipextn
            else:
                # If sky-subtracted product does not exist, use regular input
                _expname = chip.outputNames['data']
            print '-Drizzle input: ',_expname

            # Open the SCI image
            _handle = fileutil.openImage(_expname,mode='readonly',memmap=0)
            _sciext = _handle[chip.header['extname'],chip.header['extver']]

            # compute the undistorted 'natural' plate scale for this chip
            undistort = True
            if paramDict['coeffs'] in ['',' ','INDEF',None]:
                chip.wcs.sip = None
                chip.wcs.cpdis1 = None
                chip.wcs.cpdis2 = None
                chip.wcs.det2im = None
                undistort=False
            wcslin = distortion.utils.output_wcs([chip.wcs],undistort=undistort)
        
            if wcsmap is None and cdriz is not None:
                print 'Using WCSLIB-based coordinate transformation...'
                wcs_functions.applyShift_to_WCS(img,chip.wcs,outwcs)
                mapping = cdriz.DefaultWCSMapping(chip.wcs,outwcs,int(chip.size1),int(chip.size2),stepsize)                
            else:
                #
                ##Using the Python class for the WCS-based transformation
                #
                # Use user provided mapping function
                print 'Using coordinate transformation defined by user...'
                if wcsmap is None:
                    wcsmap = wcs_functions.WCSMap
                wmap = wcsmap(chip.wcs,outwcs)
                wmap.applyShift(img)
                mapping = wmap.forward

            pix_ratio = outwcs.pscale/wcslin.pscale

            ####
            #
            # Put the units keyword handling in the imageObject class
            #
            ####
            # Determine output value of BUNITS
            # and make sure it is not specified as 'ergs/cm...'
            _bunit = chip._bunit

            _bindx = _bunit.find('/')

            if paramDict['units'] == 'cps':
                # If BUNIT value does not specify count rate already...
                if _bindx < 1:
                    # ... append '/SEC' to value
                    _bunit += '/S'
                else:
                    # reset _bunit here to None so it does not
                    #    overwrite what is already in header
                    _bunit = None
            else:
                if _bindx > 0:
                    # remove '/S'
                    _bunit = _bunit[:_bindx]
                else:
                    # reset _bunit here to None so it does not
                    #    overwrite what is already in header
                    _bunit = None

            # Compute what plane of the context image this input would
            # correspond to:
            # _numchips increments as each chip is drizzled
            _planeid = int(_numchips /32)

            # Select which mask needs to be read in for drizzling
            ####
            #
            # Actually need to generate mask file here 'on-demand'
            # and combine it with the static_mask for single_drizzle case...
            #
            ####
            # Build basic DQMask from DQ array and bits value
            dqarr = img.buildMask(chip._chip,bits=bits,wht_type=wht_type)

            # Merge appropriate additional mask(s) with DQ mask
            if single:
                mergeDQarray(chip.outputNames['staticMask'],dqarr)
            else:
                mergeDQarray(chip.outputNames['staticMask'],dqarr)
                mergeDQarray(chip.outputNames['crmaskImage'],dqarr)
                updateInputDQArray(chip.dqfile,chip.dq_extn,chip._chip,chip.outputNames['crmaskImage'],crbit)

            img.set_wtscl(chip._chip,paramDict['wt_scl'])

            # Convert mask to a datatype expected by 'tdriz'
            # Also, base weight mask on ERR or IVM file as requested by user
            if wht_type == 'ERR':
                _inwht = img.buildERRmask(chip._chip,dqarr,pix_ratio)
            elif wht_type == 'IVM':
                _inwht = img.buildIVMmask(chip._chip,dqarr,pix_ratio)
            else: # wht_type == 'EXP'
                _inwht = dqarr.astype(np.float32)
            
            # Set additional parameters needed by 'drizzle'
            _in_units = chip.in_units.lower()
            if _in_units == 'cps':
                _expin = 1.0
            else:
                _expin = chip._exptime

            _shift_fr = 'output'
            _shift_un = 'output'
            _uniqid = _numchips + 1
            ystart = 0
            nmiss = 0
            nskip = 0

            _con = True
            _imgctx = _numctx['all']
            if single:
                _imgctx = _numctx[chip.outputNames['outSingle']]

            if _nplanes == 1:
                _con = False
                # We need to reset what gets passed to TDRIZ
                # when only 1 context image plane gets generated
                # to prevent overflow problems with trying to access
                # planes that weren't created for large numbers of inputs.
                _planeid = 0
                _uniqid = ((_uniqid-1) % 32) + 1

            #
            # This call to 'cdriz.tdriz' uses the new C syntax
            #
            _dny = _sciext.data.shape[0]
            # Call 'drizzle' to perform image combination
            if (_sciext.data.dtype > np.float32):
                #WARNING: Input array recast as a float32 array
                _sciext.data = _sciext.data.astype(np.float32)

            _vers,nmiss,nskip = cdriz.tdriz(_sciext.data,_inwht, _outsci, _outwht,
                _outctx[_planeid], _uniqid, ystart, 1, 1, _dny,
                pix_ratio, 1.0, 1.0, 'center', paramDict['pixfrac'],
                paramDict['kernel'], _in_units, _expin,chip._wtscl,
                fillval, nmiss, nskip, 1, mapping)

            # Set up information for generating output FITS image
            #### Check to see what names need to be included here for use in _hdrlist
            chip.outputNames['driz_version'] = _vers
            outputvals = chip.outputNames.copy()
            # Update entries for names/values based on final output
            outputvals.update(img.outputValues)
            outputvals.update(img.outputNames)
            _hdrlist.append(outputvals)

            if nmiss > 0:
                print '! Warning, ',nmiss,' points were outside the output image.'
            if nskip > 0:
                print '! Note, ',nskip,' input lines were skipped completely.'
            # Close image handle
            _handle.close()
            del _handle,_sciext
            del _inwht

            # Remember the name of the first image that goes into
            # this particular product
            # This will insure that the header reports the proper
            # values for the start of the exposure time used to make
            # this product; in particular, TIME-OBS and DATE-OBS.
            if _numchips == 0:
                _template = chip.outputNames['data']

            # Increment number of chips processed for single output
            _numchips += 1
            if _numchips == _imgctx:
                ###########################
                #
                #   IMPLEMENTATION REQUIREMENT:
                #
                # Need to implement scaling of the output image
                # from 'cps' to 'counts' in the case where 'units'
                # was set to 'counts'... 21-Mar-2005
                #
                ###########################
                # Start by determining what exposure time needs to be used
                # to rescale the product.
                if single:
                    _expscale = chip._exptime
                else:
                    _expscale = img.outputValues['texptime']

                _gnscale = 1.0
                
                # Convert output data from electrons/sec to counts/sec as specified
                if proc_units.lower() == 'native' and native_units.lower()[:6] == 'counts':
                    np.divide(_outsci, chip._gain, _outsci)
                    _bunit = native_units.lower()
                    if paramDict['units'] == 'counts': 
                        indx = _bunit.find('/')
                        if indx > 0: _bunit = _bunit[:indx]
                #If output units were set to 'counts', rescale the array in-place
                if paramDict['units'] == 'counts':
                    np.multiply(_outsci, _expscale, _outsci)

                # record IDCSCALE for output to product header
                paramDict['idcscale'] = chip.wcs.idcscale

                #
                # Write output arrays to FITS file(s) and reset chip counter
                #
                _outimg = outputimage.OutputImage(_hdrlist, paramDict, build=build, wcs=output_wcs, single=single)
                _outimg.set_bunit(_bunit)
                _outimg.set_units(paramDict['units'])

                _outimg.writeFITS(_template,_outsci,_outwht,ctxarr=_outctx,versions=_versions)
                del _outimg
                #
                # Reset chip counter for next output image...
                #
                _numchips = 0
                _nimg = 0
                np.multiply(_outsci,0.,_outsci)
                np.multiply(_outwht,0.,_outwht)
                np.multiply(_outctx,0,_outctx)

                _hdrlist = []
            else:
                _nimg += 1

    del _outsci,_outwht,_outctx, _hdrlist
    # end of loop over each chip
