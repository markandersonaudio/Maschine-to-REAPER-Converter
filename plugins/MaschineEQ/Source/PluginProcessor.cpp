#include "PluginProcessor.h"
#include "PluginEditor.h"

//==============================================================================
// Parameter layout
//==============================================================================

juce::AudioProcessorValueTreeState::ParameterLayout
MaschineEQAudioProcessor::createParameterLayout()
{
    std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "lowFreq", 1 },
            "Low Freq",
            juce::NormalisableRange<float>(
                20.0f,
                8000.0f,
                0.01f,
                0.30f),
            248.0f,
            "Hz"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "lowGain", 1 },
            "Low Gain",
            juce::NormalisableRange<float>(
                -20.0f,
                20.0f,
                0.01f),
            0.0f,
            "dB"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "lowMidFreq", 1 },
            "Low-Mid Freq",
            juce::NormalisableRange<float>(
                40.0f,
                16000.0f,
                0.01f,
                0.30f),
            630.0f,
            "Hz"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "lowMidGain", 1 },
            "Low-Mid Gain",
            juce::NormalisableRange<float>(
                -20.0f,
                20.0f,
                0.01f),
            0.0f,
            "dB"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "highMidFreq", 1 },
            "High-Mid Freq",
            juce::NormalisableRange<float>(
                40.0f,
                16000.0f,
                0.01f,
                0.30f),
            1970.0f,
            "Hz"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "highMidGain", 1 },
            "High-Mid Gain",
            juce::NormalisableRange<float>(
                -20.0f,
                20.0f,
                0.01f),
            0.0f,
            "dB"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "highFreq", 1 },
            "High Freq",
            juce::NormalisableRange<float>(
                50.0f,
                20000.0f,
                0.01f,
                0.30f),
            2610.0f,
            "Hz"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "highGain", 1 },
            "High Gain",
            juce::NormalisableRange<float>(
                -20.0f,
                20.0f,
                0.01f),
            0.0f,
            "dB"));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "lowMidWidth", 1 },
            "Low-Mid Width",
            juce::NormalisableRange<float>(
                0.1f,
                4.0f,
                0.01f),
            2.0f));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "highMidWidth", 1 },
            "High-Mid Width",
            juce::NormalisableRange<float>(
                0.1f,
                4.0f,
                0.01f),
            2.0f));

    params.push_back(
        std::make_unique<juce::AudioParameterFloat>(
            juce::ParameterID { "outputGain", 1 },
            "Output Gain",
            juce::NormalisableRange<float>(
                -20.0f,
                20.0f,
                0.01f),
            0.0f,
            "dB"));

    return {
        params.begin(),
        params.end()
    };
}

//==============================================================================
// Construction
//==============================================================================

MaschineEQAudioProcessor::MaschineEQAudioProcessor()

#ifndef JucePlugin_PreferredChannelConfigurations

    : AudioProcessor(
        BusesProperties()

    #if ! JucePlugin_IsMidiEffect

        #if ! JucePlugin_IsSynth

            .withInput(
                "Input",
                juce::AudioChannelSet::stereo(),
                true)

        #endif

            .withOutput(
                "Output",
                juce::AudioChannelSet::stereo(),
                true)

    #endif
      ),

      parameters(
          *this,
          nullptr,
          "PARAMETERS",
          createParameterLayout())

#else

    : parameters(
          *this,
          nullptr,
          "PARAMETERS",
          createParameterLayout())

#endif
{
}

MaschineEQAudioProcessor::~MaschineEQAudioProcessor()
{
}

//==============================================================================

const juce::String MaschineEQAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool MaschineEQAudioProcessor::acceptsMidi() const
{
#if JucePlugin_WantsMidiInput
    return true;
#else
    return false;
#endif
}

bool MaschineEQAudioProcessor::producesMidi() const
{
#if JucePlugin_ProducesMidiOutput
    return true;
#else
    return false;
#endif
}

bool MaschineEQAudioProcessor::isMidiEffect() const
{
#if JucePlugin_IsMidiEffect
    return true;
#else
    return false;
#endif
}

double MaschineEQAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

//==============================================================================
// Programs
//==============================================================================

int MaschineEQAudioProcessor::getNumPrograms()
{
    return 1;
}

int MaschineEQAudioProcessor::getCurrentProgram()
{
    return 0;
}

void MaschineEQAudioProcessor::setCurrentProgram(int index)
{
    juce::ignoreUnused(index);
}

const juce::String MaschineEQAudioProcessor::getProgramName(int index)
{
    juce::ignoreUnused(index);
    return {};
}

void MaschineEQAudioProcessor::changeProgramName(
    int index,
    const juce::String& newName)
{
    juce::ignoreUnused(
        index,
        newName);
}

//==============================================================================
// Prepare
//==============================================================================

void MaschineEQAudioProcessor::prepareToPlay(
    double sampleRate,
    int samplesPerBlock)
{
    currentSampleRate = sampleRate;

    juce::dsp::ProcessSpec spec;

    spec.sampleRate =
        sampleRate;

    spec.maximumBlockSize =
        static_cast<juce::uint32>(
            samplesPerBlock);

    spec.numChannels =
        static_cast<juce::uint32>(
            getTotalNumOutputChannels());

    lowShelf.prepare(spec);
    lowMidPeak.prepare(spec);
    highMidPeak.prepare(spec);
    highShelf.prepare(spec);

    eqDelay.prepare(spec);
    eqDelay.setDelay(1.0f);

    outputGain.prepare(spec);
    outputGain.setRampDurationSeconds(0.02);

    lowShelf.reset();
    lowMidPeak.reset();
    highMidPeak.reset();
    highShelf.reset();

    eqDelay.reset();
    outputGain.reset();

    updateFilters();
}

void MaschineEQAudioProcessor::releaseResources()
{
}

//==============================================================================
// Level 2 DSP model
//==============================================================================

void MaschineEQAudioProcessor::updateFilters()
{
    const float lowFreq =
        parameters.getRawParameterValue("lowFreq")->load();

    const float lowGain =
        parameters.getRawParameterValue("lowGain")->load();

    const float lowMidFreq =
        parameters.getRawParameterValue("lowMidFreq")->load();

    const float lowMidGain =
        parameters.getRawParameterValue("lowMidGain")->load();

    const float highMidFreq =
        parameters.getRawParameterValue("highMidFreq")->load();

    const float highMidGain =
        parameters.getRawParameterValue("highMidGain")->load();

    const float highFreq =
        parameters.getRawParameterValue("highFreq")->load();

    const float highGain =
        parameters.getRawParameterValue("highGain")->load();

    const float lowMidWidth =
        parameters.getRawParameterValue("lowMidWidth")->load();

    const float highMidWidth =
        parameters.getRawParameterValue("highMidWidth")->load();

    const float outputGainDb =
        parameters.getRawParameterValue("outputGain")->load();

    const float maxFreq =
        static_cast<float>(
            currentSampleRate * 0.49);

    const float safeLowFreq =
        juce::jmin(lowFreq, maxFreq);

    const float safeLowMidFreq =
        juce::jmin(lowMidFreq, maxFreq);

    const float safeHighMidFreq =
        juce::jmin(highMidFreq, maxFreq);

    const float safeHighFreq =
        juce::jmin(highFreq, maxFreq);

    //==========================================================================
    // LOW SHELF
    // Measured Maschine behaviour: RBJ-style low shelf, fixed S = 0.5
    //==========================================================================

    constexpr float lowShelfSlope = 0.5f;

    const float lowShelfA =
        std::pow(
            10.0f,
            lowGain / 40.0f);

    const float lowShelfQ =
        1.0f
        /
        std::sqrt(
            (lowShelfA + (1.0f / lowShelfA))
            *
            ((1.0f / lowShelfSlope) - 1.0f)
            +
            2.0f);

    *lowShelf.state =
        *Coefficients::makeLowShelf(
            currentSampleRate,
            safeLowFreq,
            lowShelfQ,
            juce::Decibels::decibelsToGain(
                lowGain));

    //==========================================================================
    // MASCHINE WIDTH MAPPING
    //==========================================================================

    const auto maschineWidthToBandwidth =
        [](float width)
        {
            width =
                juce::jlimit(
                    0.1f,
                    4.0f,
                    width);

            constexpr float a =
                0.03296977f;

            constexpr float b =
                -0.02935687f;

            constexpr float c =
                0.00972895f;

            return
                width
                +
                (width - 0.1f)
                *
                (4.0f - width)
                *
                (
                    a
                    +
                    b * width
                    +
                    c * width * width
                );
        };

    //==========================================================================
    // LOW-MID
    //==========================================================================

    const float lowMidBandwidth =
        maschineWidthToBandwidth(
            lowMidWidth);

    const float lowMidOmega =
        2.0f
        *
        juce::MathConstants<float>::pi
        *
        safeLowMidFreq
        /
        static_cast<float>(
            currentSampleRate);

    const float lowMidQ =
        1.0f
        /
        (
            2.0f
            *
            std::sinh(
                (std::log(2.0f) / 2.0f)
                *
                lowMidBandwidth
                *
                (
                    lowMidOmega
                    /
                    std::sin(lowMidOmega)
                )
            )
        );

    *lowMidPeak.state =
        *Coefficients::makePeakFilter(
            currentSampleRate,
            safeLowMidFreq,
            lowMidQ,
            juce::Decibels::decibelsToGain(
                lowMidGain));

    //==========================================================================
    // HIGH-MID
    //==========================================================================

    const float highMidBandwidth =
        maschineWidthToBandwidth(
            highMidWidth);

    const float highMidOmega =
        2.0f
        *
        juce::MathConstants<float>::pi
        *
        safeHighMidFreq
        /
        static_cast<float>(
            currentSampleRate);

    const float highMidQ =
        1.0f
        /
        (
            2.0f
            *
            std::sinh(
                (std::log(2.0f) / 2.0f)
                *
                highMidBandwidth
                *
                (
                    highMidOmega
                    /
                    std::sin(highMidOmega)
                )
            )
        );

    *highMidPeak.state =
        *Coefficients::makePeakFilter(
            currentSampleRate,
            safeHighMidFreq,
            highMidQ,
            juce::Decibels::decibelsToGain(
                highMidGain));

    //==========================================================================
    // HIGH SHELF
    // Measured Maschine behaviour: RBJ-style high shelf, fixed S = 0.5
    //==========================================================================

    constexpr float highShelfSlope = 0.5f;

    const float highShelfA =
        std::pow(
            10.0f,
            highGain / 40.0f);

    const float highShelfQ =
        1.0f
        /
        std::sqrt(
            (highShelfA + (1.0f / highShelfA))
            *
            ((1.0f / highShelfSlope) - 1.0f)
            +
            2.0f);

    *highShelf.state =
        *Coefficients::makeHighShelf(
            currentSampleRate,
            safeHighFreq,
            highShelfQ,
            juce::Decibels::decibelsToGain(
                highGain));

    outputGain.setGainDecibels(
        outputGainDb);
}

//==============================================================================
// Bus support
//==============================================================================

#ifndef JucePlugin_PreferredChannelConfigurations

bool MaschineEQAudioProcessor::isBusesLayoutSupported(
    const BusesLayout& layouts) const
{
#if JucePlugin_IsMidiEffect

    juce::ignoreUnused(layouts);
    return true;

#else

    if (
        layouts.getMainOutputChannelSet()
            != juce::AudioChannelSet::mono()
        &&
        layouts.getMainOutputChannelSet()
            != juce::AudioChannelSet::stereo()
       )
    {
        return false;
    }

#if ! JucePlugin_IsSynth

    if (
        layouts.getMainOutputChannelSet()
        !=
        layouts.getMainInputChannelSet()
       )
    {
        return false;
    }

#endif

    return true;

#endif
}

#endif

//==============================================================================
// Audio processing
//==============================================================================

void MaschineEQAudioProcessor::processBlock(
    juce::AudioBuffer<float>& buffer,
    juce::MidiBuffer& midiMessages)
{
    juce::ignoreUnused(midiMessages);

    juce::ScopedNoDenormals noDenormals;

    const auto totalNumInputChannels =
        getTotalNumInputChannels();

    const auto totalNumOutputChannels =
        getTotalNumOutputChannels();

    for (
        auto channel = totalNumInputChannels;
        channel < totalNumOutputChannels;
        ++channel
        )
    {
        buffer.clear(
            channel,
            0,
            buffer.getNumSamples());
    }

    updateFilters();

    juce::dsp::AudioBlock<float> block(buffer);

    juce::dsp::ProcessContextReplacing<float>
        context(block);

    eqDelay.process(context);

    lowShelf.process(context);
    lowMidPeak.process(context);
    highMidPeak.process(context);
    highShelf.process(context);

    outputGain.process(context);
}

//==============================================================================
// Editor
//==============================================================================

bool MaschineEQAudioProcessor::hasEditor() const
{
    return true;
}

juce::AudioProcessorEditor*
MaschineEQAudioProcessor::createEditor()
{
    return new MaschineEQAudioProcessorEditor(
        *this);
}

//==============================================================================
// State
//==============================================================================

void MaschineEQAudioProcessor::getStateInformation(
    juce::MemoryBlock& destData)
{
    auto state =
        parameters.copyState();

    std::unique_ptr<juce::XmlElement>
        xml(
            state.createXml());

    copyXmlToBinary(
        *xml,
        destData);
}

void MaschineEQAudioProcessor::setStateInformation(
    const void* data,
    int sizeInBytes)
{
    std::unique_ptr<juce::XmlElement>
        xmlState(
            getXmlFromBinary(
                data,
                sizeInBytes));

    if (xmlState != nullptr)
    {
        if (
            xmlState->hasTagName(
                parameters.state.getType())
           )
        {
            parameters.replaceState(
                juce::ValueTree::fromXml(
                    *xmlState));
        }
    }
}

//==============================================================================
// Plugin entry point
//==============================================================================

juce::AudioProcessor*
JUCE_CALLTYPE createPluginFilter()
{
    return new MaschineEQAudioProcessor();
}
